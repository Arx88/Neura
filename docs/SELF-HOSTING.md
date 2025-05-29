# Suna Self-Hosting Guide

This guide provides detailed instructions for setting up and hosting your own instance of Suna, an open-source generalist AI agent.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Installation Steps](#installation-steps)
- [Manual Configuration](#manual-configuration)
- [Post-Installation Steps](#post-installation-steps)
- [Troubleshooting](#troubleshooting)

## Overview

Suna consists of four main components:

1. **Backend API** - Python/FastAPI service for REST endpoints, thread management, and LLM integration
2. **Backend Worker** - Python/Dramatiq worker service for handling agent tasks
3. **Frontend** - Next.js/React application providing the user interface
4. **Agent Docker** - Isolated execution environment for each agent
5. **Supabase Database** - Handles data persistence and authentication

## Prerequisites

Before starting the installation process, you'll need to set up the following:

### 1. Supabase Project

1. Create an account at [Supabase](https://supabase.com/)
2. Create a new project
3. Note down the following information (found in Project Settings → API):
   - Project URL (e.g., `https://abcdefg.supabase.co`)
   - API keys (anon key and service role key)

### 2. API Keys

Obtain the following API keys:

#### Required

- **LLM Provider** (at least one of the following):

  - [Anthropic](https://console.anthropic.com/) - Recommended for best performance
  - [OpenAI](https://platform.openai.com/)
  - [Groq](https://console.groq.com/)
  - [OpenRouter](https://openrouter.ai/)
  - [AWS Bedrock](https://aws.amazon.com/bedrock/)

- **Search and Web Scraping**:

  - [Tavily](https://tavily.com/) - For enhanced search capabilities
  - [Firecrawl](https://firecrawl.dev/) - For web scraping capabilities

- **Agent Execution**:
  - [Daytona](https://app.daytona.io/) - For secure agent execution

#### Optional

- **RapidAPI** - For accessing additional API services (optional)

### 3. Required Software

Ensure the following tools are installed on your system. The `setup.py` wizard will attempt to automatically install many of these on Windows if they are missing, using tools like `winget` or `npm`. Manual installation is always an option.

*Windows users: It's recommended to run `python setup.py` in a terminal with administrator privileges, as this can help with the automated installation of missing software.*

- **[Git](https://git-scm.com/downloads)**: Essential for version control. The `setup.py` wizard will attempt to automatically install this on Windows using `winget` if it's missing. Manual installation is also an option.
- **[Docker](https://docs.docker.com/get-docker/)**: For containerizing Suna services and agent execution. For Docker on Windows, manual installation of Docker Desktop is required. The `setup.py` script will provide detailed instructions, including guidance on enabling WSL2 and hardware virtualization.
- **[Python 3.11](https://www.python.org/downloads/)**: The core programming language for the backend. The `setup.py` wizard will attempt to automatically install Python 3.11 on Windows using `winget` if it's missing. Manual installation is also an option.
- **[Poetry](https://python-poetry.org/docs/#installation)**: For Python dependency management. The `setup.py` wizard will attempt to automatically install this on Windows if it's missing (typically via pip or winget). Manual installation is also an option.
- **[Node.js & npm](https://nodejs.org/en/download/)**: For the frontend application. Node.js includes npm (Node Package Manager). The `setup.py` wizard will attempt to automatically install Node.js (which includes npm) on Windows using `winget` if it's missing. Manual installation is also an option.
- **[Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started)**: For managing your Supabase project migrations. The `setup.py` wizard will attempt to automatically install this on Windows via `npm install -g supabase` if it's missing. Manual installation is also an option.
- **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)**: Required for Optical Character Recognition (OCR) functionalities (e.g., extracting text from images if you intend to use features relying on `pytesseract`). The `setup.py` script will attempt to install Tesseract OCR on Windows via `winget` and guide you through manual installation if needed, emphasizing the critical step of adding Tesseract's installation directory to your system PATH.

## Installation Steps

### 1. Clone the Repository

```bash
git clone https://github.com/kortix-ai/suna.git
cd suna
```

### 2. Run the Setup Wizard

The setup wizard will guide you through the installation process:

```bash
python setup.py
```
The wizard now includes more robust checks and automated installation attempts for prerequisites on Windows (using `winget`, `npm`, etc.). If any tools are installed automatically by the script, and you encounter issues with them not being found immediately after the setup completes (e.g., a command like `poetry` or `supabase` not recognized), please try opening a new terminal window and re-running the setup script, or just proceed with the manual commands. This is because your system's PATH environment variable may need refreshing for the newly installed software to be recognized in the current session.

The wizard will:

- Check if all required tools are installed
- Collect your API keys and configuration information
- Set up the Supabase database
- Configure environment files
- Install dependencies
- Start Suna using your preferred method

### 3. Supabase Configuration

During setup, you'll need to:

1. Log in to the Supabase CLI
2. Link your local project to your Supabase project
3. Push database migrations
4. Manually expose the 'basejump' schema in Supabase:
   - Go to your Supabase project
   - Navigate to Project Settings → API
   - Add 'basejump' to the Exposed Schema section

### 4. Daytona Configuration

As part of the setup, you'll need to:

1. Create a Daytona account
2. Generate an API key
3. Create a Docker image:
   - Image name: `kortix/suna:0.1.2.8`
   - Entrypoint: `/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf`

## Manual Configuration

If you prefer to configure your installation manually, or if you need to modify the configuration after installation, here's what you need to know:

### Backend Configuration (.env)

The backend configuration is stored in `backend/.env`

Example configuration:

```sh
# Environment Mode
ENV_MODE=local

# DATABASE
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# REDIS
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_SSL=false

# RABBITMQ
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672

# LLM Providers
ANTHROPIC_API_KEY=your-anthropic-key
OPENAI_API_KEY=your-openai-key
MODEL_TO_USE=anthropic/claude-3-7-sonnet-latest

# Ollama Configuration (Example for local setup)
# OLLAMA_API_BASE=http://localhost:11434  # Required: Set this to your Ollama server URL.
# OLLAMA_API_KEY=                       # Optional: Usually left blank or omitted for local Ollama.
                                       # Only set if your Ollama instance is specifically configured to require a key.

# WEB SEARCH
TAVILY_API_KEY=your-tavily-key

# WEB SCRAPE
FIRECRAWL_API_KEY=your-firecrawl-key
FIRECRAWL_URL=https://api.firecrawl.dev

# Sandbox container provider
DAYTONA_API_KEY=your-daytona-key
DAYTONA_SERVER_URL=https://app.daytona.io/api
DAYTONA_TARGET=us

NEXT_PUBLIC_URL=http://localhost:3000
```

### Frontend Configuration (.env.local)

The frontend configuration is stored in `frontend/.env.local` and includes:

- Supabase connection details
- Backend API URL

Example configuration:

```sh
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_BACKEND_URL=http://backend:8000/api
NEXT_PUBLIC_URL=http://localhost:3000
```

## Post-Installation Steps

After completing the installation, you'll need to:

1. **Create an account** - Use Supabase authentication to create your first account
2. **Verify installations** - Check that all components are running correctly

## Startup Options

Suna can be started in two ways:

### 1. Using Docker Compose (Recommended)

This method starts all required services in Docker containers:

```bash
docker compose up -d # Use `docker compose down` to stop it later
# or
python start.py # Use the same to stop it later
```

### 2. Manual Startup

This method requires you to start each component separately:

1. Start Redis and RabbitMQ (required for backend):

```bash
docker compose up redis rabbitmq -d
```

2. Start the frontend (in one terminal):

```bash
cd frontend
npm run dev
```

3. Start the backend (in another terminal):

```bash
cd backend
poetry run python3.11 api.py
```

4. Start the worker (in one more terminal):

```bash
cd backend
poetry run python3.11 -m dramatiq run_agent_background
```

## Troubleshooting

### Common Issues

1. **Docker services not starting**

   - Check Docker logs: `docker compose logs`
   - Ensure Docker is running correctly
   - Verify port availability (3000 for frontend, 8000 for backend)

2. **Database connection issues**

   - Verify Supabase configuration
   - Check if 'basejump' schema is exposed in Supabase

3. **LLM API key issues**

   - Verify API keys are correctly entered
   - Check for API usage limits or restrictions

4. **Daytona connection issues**
   - Verify Daytona API key
   - Check if the container image is correctly configured

### Logs

To view logs and diagnose issues:

```bash
# Docker Compose logs
docker compose logs -f

# Frontend logs (manual setup)
cd frontend
npm run dev

# Backend logs (manual setup)
cd backend
poetry run python3.11 api.py

# Worker logs (manual setup)
cd backend
poetry run python3.11 -m dramatiq run_agent_background
```

---

For further assistance, join the [Suna Discord Community](https://discord.gg/Py6pCBUUPw) or check the [GitHub repository](https://github.com/kortix-ai/suna) for updates and issues.
