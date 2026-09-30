from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import torch


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class IndicTrans2Adapter:
    """Resident IndicTrans2 adapter optimized for short UI text and long lessons.

    Key invariants:
    - never silently truncate a lesson at tokenizer max length;
    - short requests use greedy decoding and finish as soon as EOS appears;
    - long lessons are split into tokenizer-safe chunks and batched;
    - model routes stay warm across requests;
    - repeated translations use an in-memory cache.
    """

    def __init__(
        self,
        model_root: Path,
        cpu_only: bool = False,
        max_chars: int = 12000,
        text_beams: int = 1,
        route_cache_limit: int = 2,
        max_new_tokens: int = 512,
        live_max_new_tokens: int = 96,
        input_token_budget: int = 220,
        batch_size: int = 4,
        inference_lock_enabled: bool = True,
    ) -> None:
        self.model_root = Path(model_root)
        self.cpu_only = cpu_only
        self.max_chars = max(1200, int(max_chars))
        self.text_beams = max(1, min(int(text_beams), 5))
        self.live_beams = max(1, min(int(text_beams), 3))
        self.max_new_tokens = max(96, min(int(max_new_tokens), 640))
        self.live_max_new_tokens = max(32, min(int(live_max_new_tokens), 160))
        self.input_token_budget = max(128, min(int(input_token_budget), 240))
        self.batch_size = max(1, min(int(batch_size), 8))
        self._inference_lock = threading.RLock()
        self._inference_lock_enabled = bool(inference_lock_enabled)
        self._bundles: dict[str, tuple[Any, Any, Any, torch.device]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()
        self._cache: OrderedDict[tuple[str, str, str, int], str] = OrderedDict()
        self._cache_limit = 512
        self._route_limit = max(1, min(int(route_cache_limit), 3))
        self._route_order: OrderedDict[str, None] = OrderedDict()

    @staticmethod
    def _key(src: str, tgt: str) -> str:
        if src.startswith('eng') and not tgt.startswith('eng'):
            return 'en-indic'
        if not src.startswith('eng') and tgt.startswith('eng'):
            return 'indic-en'
        return 'indic-indic'

    def _path(self, key: str) -> Path:
        return self.model_root / 'translation' / key

    @staticmethod
    def _resolve_model_dir(path: Path) -> Path:
        if (path / 'config.json').is_file():
            return path
        if not path.is_dir():
            return path
        configs = [p for p in path.iterdir() if p.is_dir() and (p / 'config.json').is_file()]
        return configs[0] if len(configs) == 1 else path

    def _get_lock(self, key: str) -> threading.RLock:
        with self._registry_lock:
            return self._locks.setdefault(key, threading.RLock())

    def health(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for key in ('indic-indic', 'en-indic', 'indic-en'):
            path = self._resolve_model_dir(self._path(key))
            has_config = (path / 'config.json').is_file()
            has_weights = any(path.glob('*.safetensors')) or any(
                (path / name).is_file() for name in ('pytorch_model.bin', 'model.bin')
            )
            has_tokenizer = any(
                (path / name).is_file()
                for name in ('tokenizer.json', 'tokenizer.model', 'sentencepiece.bpe.model', 'tokenizer_config.json')
            )
            result[key] = bool(path.is_dir() and has_config and has_weights and has_tokenizer)
        return result

    def loaded(self) -> dict[str, bool]:
        with self._registry_lock:
            return {key: key in self._bundles for key in ('indic-indic', 'en-indic', 'indic-en')}

    def _load(self, key: str):
        lock = self._get_lock(key)
        with lock:
            if key in self._bundles:
                with self._registry_lock:
                    self._route_order.pop(key, None)
                    self._route_order[key] = None
                return self._bundles[key]

            path = self._resolve_model_dir(self._path(key))
            if not path.is_dir() or not (path / 'config.json').is_file():
                raise FileNotFoundError(f'IndicTrans2 model folder is incomplete: {path}')

            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
            try:
                from IndicTransToolkit.processor import IndicProcessor
            except Exception:
                try:
                    from IndicTransToolkit import IndicProcessor
                except Exception as exc:
                    raise RuntimeError('IndicProcessor could not be imported. Install IndicTransToolkit==1.1.1.') from exc

            device = torch.device('cpu' if self.cpu_only or not torch.cuda.is_available() else 'cuda')
            tokenizer = AutoTokenizer.from_pretrained(str(path), trust_remote_code=True, local_files_only=True)
            kwargs: dict[str, Any] = {'trust_remote_code': True, 'local_files_only': True}
            if device.type == 'cuda':
                kwargs['torch_dtype'] = torch.float16
            model = AutoModelForSeq2SeqLM.from_pretrained(str(path), **kwargs)
            model.to(device).eval()
            processor = IndicProcessor(inference=True)
            bundle = (tokenizer, model, processor, device)
            self._bundles[key] = bundle
            with self._registry_lock:
                self._route_order.pop(key, None)
                self._route_order[key] = None
                while len(self._route_order) > self._route_limit:
                    old_key, _ = self._route_order.popitem(last=False)
                    if old_key != key:
                        self._bundles.pop(old_key, None)
            return bundle

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r'\s+', ' ', (text or '').strip())

    @staticmethod
    def _sentence_split(text: str) -> list[str]:
        normalized = IndicTrans2Adapter._normalize(text)
        if not normalized:
            return []
        parts = re.split(r'(?<=[.!?।॥])\s+', normalized)
        return [part.strip() for part in parts if part.strip()]

    def _token_count(self, text: str, src: str, tgt: str, tokenizer: Any, processor: Any) -> int:
        prepared = processor.preprocess_batch([text], src_lang=src, tgt_lang=tgt, visualize=False)
        encoded = tokenizer(prepared, padding=False, truncation=False, return_tensors='pt')
        return int(encoded['input_ids'].shape[1])

    def _split_words_to_budget(self, text: str, src: str, tgt: str, tokenizer: Any, processor: Any) -> list[str]:
        words = text.split()
        if not words:
            return []
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            candidate = ' '.join(current + [word])
            if current and self._token_count(candidate, src, tgt, tokenizer, processor) > self.input_token_budget:
                chunks.append(' '.join(current).strip())
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(' '.join(current).strip())
        # Extremely pathological single tokens are kept as-is. This is still
        # preferable to silently dropping text; the downstream model will report
        # a concrete tokenizer/model error instead of truncating.
        return chunks

    def _token_safe_chunks(self, text: str, src: str, tgt: str, tokenizer: Any, processor: Any) -> list[str]:
        sentences = self._sentence_split(text)
        if not sentences:
            return []
        chunks: list[str] = []
        current = ''
        for sentence in sentences:
            candidate = sentence if not current else f'{current} {sentence}'
            try:
                count = self._token_count(candidate, src, tgt, tokenizer, processor)
            except Exception:
                count = self.input_token_budget + 1
            if count <= self.input_token_budget:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ''
            # A single sentence may still exceed the model-safe budget. Split it
            # by words and continue from there.
            chunks.extend(self._split_words_to_budget(sentence, src, tgt, tokenizer, processor))
        if current:
            chunks.append(current)
        return chunks or [self._normalize(text)]

    def _cache_get(self, key: tuple[str, str, str, int]) -> str | None:
        with self._registry_lock:
            value = self._cache.get(key)
            if value is None:
                return None
            self._cache.move_to_end(key)
            return value

    def _cache_put(self, key: tuple[str, str, str, int], value: str) -> None:
        with self._registry_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_limit:
                self._cache.popitem(last=False)

    @staticmethod
    def _is_eos_finished(sequence: torch.Tensor, eos_token_id: Any) -> bool:
        if sequence.numel() == 0 or eos_token_id is None:
            return True
        last = int(sequence[-1].item())
        if isinstance(eos_token_id, (list, tuple, set)):
            return last in {int(item) for item in eos_token_id}
        return last == int(eos_token_id)

    def _translate_batch(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        beams: int,
        max_new_tokens: int | None = None,
    ) -> list[str]:
        tokenizer, model, processor, device = self._load(self._key(src, tgt))
        batch = processor.preprocess_batch(texts, src_lang=src, tgt_lang=tgt, visualize=False)
        encoded = tokenizer(
            batch,
            padding='longest',
            truncation=False,
            return_tensors='pt',
            return_attention_mask=True,
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        token_limit = max(96, min(int(max_new_tokens or self.max_new_tokens), 640))
        longest_input = int(encoded['input_ids'].shape[1])
        adaptive_limit = max(96, min(token_limit, int(longest_input * 2.4) + 32))
        generation_lock = self._inference_lock if self._inference_lock_enabled else _NullLock()

        with generation_lock:
            with torch.inference_mode():
                outputs = model.generate(
                    **encoded,
                    use_cache=True,
                    do_sample=False,
                    num_beams=max(1, min(beams, 5)),
                    num_return_sequences=1,
                    max_new_tokens=adaptive_limit,
                )

        translations = tokenizer.batch_decode(
            outputs.detach().cpu().tolist(),
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )
        translations = processor.postprocess_batch(translations, lang=tgt)

        # Guard against the exact failure seen in the UI: a lesson that reaches
        # the generation ceiling and gets cut mid-sentence. Retry only affected
        # items with a larger ceiling; normal short text pays no extra cost.
        eos_id = getattr(tokenizer, 'eos_token_id', None)
        retry_indexes: list[int] = []
        if outputs.ndim == 2:
            for index, seq in enumerate(outputs):
                if int(seq.shape[0]) >= max(1, adaptive_limit - 1) and not self._is_eos_finished(seq, eos_id):
                    retry_indexes.append(index)

        if retry_indexes and adaptive_limit < token_limit:
            retry_texts = [texts[index] for index in retry_indexes]
            retry_limit = min(token_limit, max(adaptive_limit * 2, adaptive_limit + 96))
            retry_batch = processor.preprocess_batch(retry_texts, src_lang=src, tgt_lang=tgt, visualize=False)
            retry_encoded = tokenizer(
                retry_batch,
                padding='longest',
                truncation=False,
                return_tensors='pt',
                return_attention_mask=True,
            )
            retry_encoded = {key: value.to(device) for key, value in retry_encoded.items()}
            with generation_lock:
                with torch.inference_mode():
                    retry_outputs = model.generate(
                        **retry_encoded,
                        use_cache=True,
                        do_sample=False,
                        num_beams=max(1, min(beams, 5)),
                        num_return_sequences=1,
                        max_new_tokens=retry_limit,
                    )
            retry_decoded = tokenizer.batch_decode(
                retry_outputs.detach().cpu().tolist(),
                skip_special_tokens=True,
                clean_up_tokenization_spaces=True,
            )
            retry_translated = processor.postprocess_batch(retry_decoded, lang=tgt)
            for local_index, original_index in enumerate(retry_indexes):
                translations[original_index] = retry_translated[local_index]

        if len(translations) != len(texts):
            raise RuntimeError(f'IndicTrans2 returned {len(translations)} outputs for {len(texts)} inputs.')
        return [item.strip() for item in translations]

    def translate_many(
        self,
        texts: list[str],
        src: str,
        tgt: str,
        *,
        beams: int | None = None,
        max_new_tokens: int | None = None,
    ) -> list[str]:
        normalized = [self._normalize(item) for item in texts]
        if not any(normalized):
            return ['' for _ in texts]
        if src == tgt:
            return normalized

        beam_count = max(1, min(int(beams if beams is not None else self.text_beams), 5))
        results: list[str | None] = [None] * len(normalized)
        pending_originals: list[int] = []
        pending_texts: list[str] = []
        for index, text in enumerate(normalized):
            if not text:
                results[index] = ''
                continue
            cache_key = (src, tgt, text, beam_count)
            cached = self._cache_get(cache_key)
            if cached is not None:
                results[index] = cached
            else:
                pending_originals.append(index)
                pending_texts.append(text)

        if not pending_texts:
            return [value or '' for value in results]

        tokenizer, _, processor, _ = self._load(self._key(src, tgt))
        chunk_records: list[tuple[int, str]] = []
        for original_index, text in zip(pending_originals, pending_texts):
            chunks = self._token_safe_chunks(text, src, tgt, tokenizer, processor)
            chunk_records.extend((original_index, chunk) for chunk in chunks)

        translated_parts: dict[int, list[str]] = {index: [] for index in pending_originals}
        chunk_texts = [chunk for _, chunk in chunk_records]
        chunk_owners = [owner for owner, _ in chunk_records]
        for start in range(0, len(chunk_texts), self.batch_size):
            batch_texts = chunk_texts[start:start + self.batch_size]
            batch_owners = chunk_owners[start:start + self.batch_size]
            translated = self._translate_batch(
                batch_texts,
                src,
                tgt,
                beam_count,
                max_new_tokens=max_new_tokens,
            )
            for owner, value in zip(batch_owners, translated):
                if value.strip():
                    translated_parts[owner].append(value.strip())

        for original_index, text in zip(pending_originals, pending_texts):
            combined = ' '.join(part for part in translated_parts[original_index] if part).strip()
            if not combined:
                raise RuntimeError('IndicTrans2 returned an empty translation.')
            results[original_index] = combined
            self._cache_put((src, tgt, text, beam_count), combined)

        return [value or '' for value in results]

    def translate(
        self,
        text: str,
        src: str,
        tgt: str,
        *,
        beams: int | None = None,
        max_new_tokens: int | None = None,
    ) -> str:
        result = self.translate_many([text], src, tgt, beams=beams, max_new_tokens=max_new_tokens)
        if not result or not result[0]:
            raise RuntimeError('IndicTrans2 returned an empty translation.')
        return result[0]

    def warm(self, src: str, tgt: str, *, beams: int | None = None, max_new_tokens: int | None = None) -> dict[str, object]:
        if src == tgt:
            return {'route': 'bypass', 'loaded': True}
        key = self._key(src, tgt)
        self._load(key)
        return {'route': key, 'loaded': True, 'device': str(self._bundles[key][3])}

    def unload(self) -> None:
        with self._registry_lock:
            self._bundles.clear()
            self._route_order.clear()
            self._cache.clear()
            self._locks.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
