"use client";

import { useState, useCallback, Fragment } from "react";
import { Trash2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MentionInput } from "./mention-input";
import { addComment, deleteComment } from "@/lib/api";
import type { FindingComment, Collaborator } from "@/lib/types";

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Render comment text with @mentions highlighted. Matches known collaborator
 * names (which may contain spaces) first, then falls back to a single word. */
function renderText(text: string, collaborators: Collaborator[]) {
  const names = collaborators
    .map((c) => c.name || c.email)
    .filter((n): n is string => !!n)
    .sort((a, b) => b.length - a.length)
    .map(escapeRegex);
  const namePattern = names.length > 0 ? `@(?:${names.join("|")})|` : "";
  const pattern = new RegExp(`(${namePattern}@\\w+)`, "g");
  const parts = text.split(pattern);
  return parts.map((part, i) =>
    part && part.startsWith("@") ? (
      <span key={i} className="text-blue-600 dark:text-blue-400 font-medium">{part}</span>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

interface FindingCommentsProps {
  findingId: string;
  comments: FindingComment[];
  projectId: string;
  collaborators: Collaborator[];
  currentUserId: string;
  currentUserName: string;
  onCommentAdded: (comment: FindingComment) => void;
  onCommentDeleted: (commentId: string, findingId: string) => void;
}

export function FindingComments({
  findingId,
  comments,
  projectId,
  collaborators,
  currentUserId,
  currentUserName,
  onCommentAdded,
  onCommentDeleted,
}: FindingCommentsProps) {
  const [text, setText] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const comment = await addComment(projectId, findingId, trimmed, currentUserName, mentions);
      onCommentAdded(comment);
      setText("");
      setMentions([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setSubmitting(false);
    }
  }, [text, mentions, submitting, projectId, findingId, currentUserName, onCommentAdded]);

  const handleDelete = useCallback(
    async (commentId: string) => {
      await deleteComment(projectId, commentId);
      onCommentDeleted(commentId, findingId);
    },
    [projectId, findingId, onCommentDeleted],
  );

  const handleMention = useCallback((userId: string) => {
    setMentions((prev) => (prev.includes(userId) ? prev : [...prev, userId]));
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="mt-3 pt-3 border-t border-border/50 space-y-2">
      {comments.map((c) => (
        <div key={c.comment_id} className="flex items-start gap-2 group">
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-1.5">
              <span className="text-xs font-medium">{c.user_name}</span>
              <span className="text-[10px] text-muted-foreground">
                {formatRelativeTime(c.created_at)}
              </span>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed mt-0.5">
              {renderText(c.text, collaborators)}
            </p>
          </div>
          {c.user_id === currentUserId && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 w-6 p-0 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
              onClick={() => handleDelete(c.comment_id)}
            >
              <Trash2 className="h-3 w-3 text-muted-foreground" />
            </Button>
          )}
        </div>
      ))}
      <div className="flex items-center gap-1.5">
        <MentionInput
          value={text}
          onChange={setText}
          onMention={handleMention}
          collaborators={collaborators}
          placeholder="Add a comment... (@ to mention)"
          onKeyDown={handleKeyDown}
          className="flex-1"
        />
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0 shrink-0"
          onClick={handleSubmit}
          disabled={!text.trim() || submitting}
        >
          <Send className="h-3.5 w-3.5" />
        </Button>
      </div>
      {error && (
        <p className="text-[11px] text-rose-600 dark:text-rose-400">{error}</p>
      )}
    </div>
  );
}
