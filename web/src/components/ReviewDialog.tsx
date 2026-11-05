import { useEffect, useRef } from "react";
import { type PackageRecord, changedFields, relativePath } from "../sample";

interface Props {
  open: boolean;
  originals: readonly PackageRecord[];
  drafts: readonly PackageRecord[];
  onClose: () => void;
  onDiscard: () => void;
}

export function ReviewDialog({ open, originals, drafts, onClose, onDiscard }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal?.();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  const lines: string[] = [];
  let count = 0;
  drafts.forEach((draft, i) => {
    const original = originals[i];
    if (!original) return;
    const fields = changedFields(original, draft);
    if (fields.length === 0) return;
    count += 1;
    lines.push(relativePath(draft));
    for (const field of fields) {
      lines.push(`- ${field}: ${JSON.stringify(original[field])}`);
      lines.push(`+ ${field}: ${JSON.stringify(draft[field])}`);
    }
    lines.push("");
  });

  return (
    <dialog ref={ref} aria-labelledby="review-title" onClose={onClose}>
      <div className="dialog-body">
        <div className="dialog-title">
          <div>
            <p className="eyebrow">Changes</p>
            <h2 id="review-title">Review your draft</h2>
          </div>
          <button type="button" aria-label="Close review" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="review-path">
          {count ? `${count} modified sample ${count === 1 ? "record" : "records"}` : "Sample workspace · no files changed"}
        </p>
        <pre className="diff">
          {lines.length ? lines.join("\n") : "No changes yet. Edit a package to explore the review flow."}
        </pre>
        <p className="checks">Preview only · validation and revision checks arrive with editing.</p>
      </div>
      <div className="dialog-footer">
        <button type="button" disabled={count === 0} onClick={onDiscard}>
          Discard drafts
        </button>
        <button type="button" className="primary" onClick={onClose}>
          Keep exploring
        </button>
      </div>
    </dialog>
  );
}
