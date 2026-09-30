import json, re
import torch
from .model_manager import manager

LANGS={"hin_Deva":"Hindi","sat_Olck":"Santhali","eng_Latn":"English"}

def _normalize(text): return re.sub(r"\s+"," ",text.strip())

def translate(text, src, tgt):
    text=_normalize(text)
    if not text: return ""
    if src==tgt: return text
    if src.startswith("eng") and not tgt.startswith("eng"): key="en-indic"
    elif not src.startswith("eng") and tgt.startswith("eng"): key="indic-en"
    else: key="indic-indic"
    tok,model=manager.translation_model(key)
    from IndicTransToolkit.processor import IndicProcessor
    processor=IndicProcessor(inference=True)
    batch=processor.preprocess_batch([text],src_lang=src,tgt_lang=tgt)
    inputs=tok(batch,return_tensors="pt",padding=True,truncation=True).to(model.device)
    with torch.inference_mode():
        out=model.generate(**inputs,max_length=256,num_beams=5,num_return_sequences=1)
    decoded=tok.batch_decode(out,skip_special_tokens=True)
    return processor.postprocess_batch(decoded,lang=tgt)[0]

def guard(source, target, src, tgt):
    flags=[]
    if not target.strip(): flags.append("EMPTY_OUTPUT")
    nums_src=re.findall(r"\b\d+(?:[.,]\d+)?\b",source); nums_tgt=re.findall(r"\b\d+(?:[.,]\d+)?\b",target)
    if nums_src and nums_src!=nums_tgt: flags.append("NUMBER_MISMATCH")
    if len(target.split())<max(1,len(source.split())//5): flags.append("VERY_SHORT_OUTPUT")
    confidence=max(0.35,0.96-0.16*len(flags))
    return {"confidence":confidence,"flags":flags,"verification_status":"UNVERIFIED" if flags else "REVIEW_REQUIRED"}
