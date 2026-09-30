# BhashaSaathi

### AI-Powered Vernacular Learning and Translation Platform

BhashaSaathi is a multilingual educational platform designed to help teachers
create and publish learning content across Indian languages.

The system combines translation, speech technologies, teacher verification,
practice generation, and live translation into a single workflow.

## Supported Languages

- English
- Hindi
- Marathi

## Core Features

- Multilingual lesson translation
- Teacher review and approval
- Speech-to-text input
- Text-to-speech audio generation
- Live translation
- Practice and worksheet generation
- Student and teacher workflows
- Offline-aware web application

## Tech Stack

### Frontend
- React
- TypeScript
- Vite

### Backend
- Python
- FastAPI
- SQLAlchemy
- Alembic

### AI / ML
- IndicTrans2
- Whisper
- Parler-TTS

### Database
- SQLite / local storage

## Project Structure

```text
apps/
├── api/       # FastAPI backend
└── web/       # React frontend

scripts/       # Development and verification utilities
core/          # Shared project logic
data/          # Project data
storage/       # Local storage
