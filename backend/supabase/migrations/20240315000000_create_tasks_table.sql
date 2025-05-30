-- Migration to create the tasks table

CREATE TABLE IF NOT EXISTS public.tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0.0,
    "startTime" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "endTime" TIMESTAMPTZ,
    "parentId" uuid REFERENCES public.tasks(id) ON DELETE SET NULL, -- Allow parent to be deleted without deleting child, child just loses parent link
    subtasks JSONB DEFAULT '[]'::jsonb,
    dependencies JSONB DEFAULT '[]'::jsonb,
    "assignedTools" JSONB DEFAULT '[]'::jsonb,
    artifacts JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    error TEXT,
    result JSONB,
    -- Timestamps for record changes
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Create a trigger to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
   NEW.updated_at = now();
   RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_tasks_updated_at
BEFORE UPDATE ON public.tasks
FOR EACH ROW
EXECUTE FUNCTION public.update_updated_at_column();

-- Indexes for commonly queried fields
CREATE INDEX IF NOT EXISTS idx_tasks_status ON public.tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parentId ON public.tasks("parentId");
CREATE INDEX IF NOT EXISTS idx_tasks_startTime ON public.tasks("startTime");

COMMENT ON TABLE public.tasks IS 'Stores state and metadata for tasks and subtasks.';
COMMENT ON COLUMN public.tasks.id IS 'Unique identifier for the task.';
COMMENT ON COLUMN public.tasks.name IS 'Human-readable name for the task.';
COMMENT ON COLUMN public.tasks.description IS 'Optional detailed description of the task.';
COMMENT ON COLUMN public.tasks.status IS 'Current status of the task (e.g., pending, running, completed, failed).';
COMMENT ON COLUMN public.tasks.progress IS 'Task progress from 0.0 to 1.0.';
COMMENT ON COLUMN public.tasks.startTime IS 'Timestamp when the task was started or created.';
COMMENT ON COLUMN public.tasks.endTime IS 'Timestamp when the task was completed or terminated.';
COMMENT ON COLUMN public.tasks.parentId IS 'Identifier of the parent task, if this is a subtask.';
COMMENT ON COLUMN public.tasks.subtasks IS 'JSON array of subtask IDs.';
COMMENT ON COLUMN public.tasks.dependencies IS 'JSON array of prerequisite task IDs.';
COMMENT ON COLUMN public.tasks.assignedTools IS 'JSON array of tools assigned or relevant to this task.';
COMMENT ON COLUMN public.tasks.artifacts IS 'JSON array of artifacts produced or used by this task.';
COMMENT ON COLUMN public.tasks.metadata IS 'JSON object for any other custom data related to the task.';
COMMENT ON COLUMN public.tasks.error IS 'Error message if the task failed.';
COMMENT ON COLUMN public.tasks.result IS 'JSON object storing the outcome or product of the task.';

-- Enable RLS for the tasks table
ALTER TABLE public.tasks ENABLE ROW LEVEL SECURITY;

-- Policies for RLS (example: allow users to manage their own tasks if you have user association)
-- For now, assuming service_role or authenticated users can access.
-- More specific RLS policies would depend on how users are associated with tasks.
-- Example: CREATE POLICY "Allow all access for service_role" ON public.tasks FOR ALL USING (true) WITH CHECK (true);
-- If you have a user_id column in tasks associated with auth.users:
-- CREATE POLICY "Users can manage their own tasks"
--   ON public.tasks FOR ALL
--   USING (auth.uid() = user_id)
--   WITH CHECK (auth.uid() = user_id);

-- For simplicity in this step, we'll rely on access through the service_role key.
-- Ensure appropriate RLS is set up based on application needs.
-- Default behavior without specific policies might restrict access.
-- For a backend service, you often operate with service_role bypassing RLS,
-- but it's good practice to define policies.

-- Let's add a basic policy that allows authenticated users to do everything for now.
-- This should be refined based on actual security requirements.
CREATE POLICY "Allow all for authenticated users" ON public.tasks
    FOR ALL
    USING (auth.role() = 'authenticated')
    WITH CHECK (auth.role() = 'authenticated');

-- If your service primarily uses a service_role key, that key bypasses RLS by default.
-- If you are using user-specific JWTs for your backend, then the above policy might be relevant.

-- Grant usage on schema and all tables in schema for anon and authenticated roles
-- This is generally handled by Supabase default permissions, but explicit grants can be added.
-- GRANT USAGE ON SCHEMA public TO anon, authenticated;
-- GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role; -- service_role has this by default
-- GRANT SELECT, INSERT, UPDATE, DELETE ON public.tasks TO authenticated;
-- GRANT SELECT ON public.tasks TO anon; -- If public read access is desired

logger.info('Supabase migration for tasks table created.');
