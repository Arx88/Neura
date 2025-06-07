'use client';

import { cn } from '@/lib/utils';
import { Brain } from 'lucide-react'; // Using Brain icon as an example
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Markdown } from '../../ui/markdown';
import { useTextStream, type Mode } from './response-stream';
import React from 'react'; // Ensure React is imported for JSX

export type ReasoningProps = {
  children?: React.ReactNode; // Made optional as text prop can also define content
  className?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  // Props for ReasoningResponse if it's directly used here
  text?: string | AsyncIterable<string>;
  speed?: number;
  mode?: Mode;
  onComplete?: () => void;
};

function Reasoning({
  children,
  className,
  open,
  onOpenChange,
  text,
  speed,
  mode,
  onComplete,
}: ReasoningProps) {
  return (
    <Collapsible
      open={open}
      onOpenChange={onOpenChange}
      className={cn(
        'bg-slate-100 dark:bg-slate-800 p-4 rounded-lg',
        className,
      )}
    >
      <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-2">
        <Brain className="h-5 w-5" />
        <span className="font-semibold">Pensamiento del Agente</span>
        {/* Chevron is usually handled by Radix CollapsibleTrigger if it's styled to include one,
            or can be added here if needed. For now, relying on default behavior or theme styling. */}
      </CollapsibleTrigger>
      <CollapsibleContent>
        {/* If ReasoningResponse is the standard content: */}
        {text ? (
          <ReasoningResponse
            text={text}
            speed={speed}
            mode={mode}
            onComplete={onComplete}
          />
        ) : (
          children // Fallback for other types of children
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

export type ReasoningResponseProps = {
  text: string | AsyncIterable<string>;
  className?: string;
  speed?: number;
  mode?: Mode;
  onComplete?: () => void;
  // Removed fadeDuration, segmentDelay, characterChunkSize for brevity from props,
  // but they are still passed to useTextStream if defined.
  fadeDuration?: number;
  segmentDelay?: number;
  characterChunkSize?: number;
};

function ReasoningResponse({
  text,
  className,
  speed = 20, // Default values kept
  mode = 'typewriter', // Default values kept
  onComplete,
  fadeDuration, // Will be passed to useTextStream
  segmentDelay, // Will be passed to useTextStream
  characterChunkSize, // Will be passed to useTextStream
}: ReasoningResponseProps) {
  const { displayedText } = useTextStream({
    textStream: text,
    speed,
    mode,
    onComplete,
    fadeDuration,
    segmentDelay,
    characterChunkSize,
  });

  return (
    <div
      className={cn(
        'text-muted-foreground prose prose-sm dark:prose-invert text-sm pt-2', // Added pt-2 for spacing
        className,
      )}
      // Removed opacity style, CollapsibleContent handles visibility
    >
      <Markdown>{displayedText}</Markdown>
    </div>
  );
}

// Exporting the main Reasoning component and ReasoningResponse.
// ReasoningTrigger and ReasoningContent are now internal to Reasoning's structure.
export { Reasoning, ReasoningResponse };
