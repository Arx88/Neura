# Suna Backend

## Running the backend

Within the backend directory, run the following command to stop and start the backend:

```bash
docker compose down && docker compose up --build
```

## Running Individual Services

You can run individual services from the docker-compose file. This is particularly useful during development:

### Running only Redis and RabbitMQ

```bash
docker compose up redis rabbitmq
```

### Running only the API and Worker

```bash
docker compose up api worker
```

## Development Setup

For local development, you might only need to run Redis and RabbitMQ, while working on the API locally. This is useful when:

- You're making changes to the API code and want to test them directly
- You want to avoid rebuilding the API container on every change
- You're running the API service directly on your machine

To run just Redis and RabbitMQ for development:```bash
docker compose up redis rabbitmq

Then you can run your API service locally with the following commands

```sh
# On one terminal
cd backend
poetry run python3.11 api.py

# On another terminal
cd frontend
poetry run python3.11 -m dramatiq run_agent_background
```

### Environment Configuration

When running services individually, make sure to:

1. Check your `.env` file and adjust any necessary environment variables
2. Ensure Redis connection settings match your local setup (default: `localhost:6379`)
3. Ensure RabbitMQ connection settings match your local setup (default: `localhost:5672`)
4. Update any service-specific environment variables if needed

### Important: Redis Host Configuration

When running the API locally with Redis in Docker, you need to set the correct Redis host in your `.env` file:

- For Docker-to-Docker communication (when running both services in Docker): use `REDIS_HOST=redis`
- For local-to-Docker communication (when running API locally): use `REDIS_HOST=localhost`

### Important: RabbitMQ Host Configuration

When running the API locally with Redis in Docker, you need to set the correct RabbitMQ host in your `.env` file:

- For Docker-to-Docker communication (when running both services in Docker): use `RABBITMQ_HOST=rabbitmq`
- For local-to-Docker communication (when running API locally): use `RABBITMQ_HOST=localhost`

Example `.env` configuration for local development:

```sh
REDIS_HOST=localhost (instead of 'redis')
REDIS_PORT=6379
REDIS_PASSWORD=

RABBITMQ_HOST=localhost (instead of 'rabbitmq')
RABBITMQ_PORT=5672
```

---

## Supabase Setup

This project uses Supabase for its database. You can set it up locally using Docker or use a cloud-hosted Supabase instance.

### Local Setup (Docker)

1.  **Ensure Docker is running.**
2.  **Configure Supabase in `backend/docker-compose.yml`:**
    *   A Supabase service definition is included in `backend/docker-compose.yml`.
    *   Make sure to set a strong password for `POSTGRES_PASSWORD` in this file.
3.  **Configure Supabase connection details in `backend/.env`:**
    *   Copy `backend/.env.example` to `backend/.env`.
    *   Update `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` with the appropriate values for your local Supabase setup.
        *   `SUPABASE_URL`: Typically `http://localhost:5432` if using the default port mapping.
        *   `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY`: You can get these from the Supabase Studio (usually available at `http://localhost:8000` after starting Supabase) or via the Supabase CLI.
4.  **Start all services:**
    ```bash
    cd backend
    docker-compose up -d --build
    ```
5.  **Run database migrations:**
    *   Install the Supabase CLI if you haven't already: `npm install supabase --save-dev` (or globally).
    *   Link your local Supabase instance: `npx supabase link --project-ref <your-project-id>` (You can find `<your-project-id>` in `backend/supabase/config.toml` or when you initialize Supabase).
    *   Apply migrations: `npx supabase db push`

### Cloud-Hosted Supabase

1.  **Create a Supabase project** on [supabase.com](https://supabase.com).
2.  **Configure Supabase connection details in `backend/.env`:**
    *   Copy `backend/.env.example` to `backend/.env`.
    *   Update `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` with the values from your Supabase project settings.
3.  **Run database migrations:**
    *   Install the Supabase CLI.
    *   Link your Supabase project: `npx supabase link --project-ref <your-project-ref>`
    *   Apply migrations: `npx supabase db push`

---

## Production Setup

For production deployments, use the following command to set resource limits

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
