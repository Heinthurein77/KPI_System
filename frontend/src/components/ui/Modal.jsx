import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

// Generic form/content dialog, following the ConfirmDialog pattern:
// portal to document.body (so no transformed ancestor shrinks it),
// Escape closes, backdrop click closes. Unlike ConfirmDialog this has no
// built-in action buttons — the content (e.g. a form) owns its own.
// Accessibility (m23): the first focusable control is focused on open,
// and Tab is trapped inside the dialog while it is open.
export default function Modal({ open, title, onClose, children, wide = false }) {
  const dialogRef = useRef(null);

  // `onClose` is normally an inline arrow (`() => setShowX(false)`), so its
  // identity changes on every parent render — including every keystroke in a
  // form inside the dialog. The focus effect below must NOT depend on it:
  // with `onClose` in the dep list the effect re-ran (and re-focused the
  // first control, the Close button) after every keystroke, stealing focus
  // out of the field being typed in — and a following Space/Enter then
  // activated that Close button and dismissed the dialog. Keep the latest
  // callback in a ref and depend on `open` only.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    function focusableItems() {
      if (!dialog) return [];
      return [...dialog.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )].filter((el) => !el.disabled);
    }
    const first = focusableItems()[0];
    // Land keyboard users where the work is: the first form control when
    // the dialog has a form (name field, person select, …), otherwise the
    // first focusable element (e.g. the Close button on content dialogs).
    const firstControl = dialog.querySelector("input, select, textarea");
    const target = firstControl && !firstControl.disabled ? firstControl : first;
    if (target) target.focus();
    function handleKey(e) {
      if (e.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusableItems();
      if (items.length === 0) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstItem) {
        e.preventDefault();
        lastItem.focus();
      } else if (!e.shiftKey && document.activeElement === lastItem) {
        e.preventDefault();
        firstItem.focus();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-sm px-4 animate-fade-in"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        className={`w-full ${wide ? "max-w-2xl" : "max-w-lg"} max-h-[90vh] overflow-y-auto rounded-xl bg-white shadow-2xl animate-dialog-in`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 px-6 py-4 border-b border-slate-100">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-6">{children}</div>
      </div>
    </div>,
    document.body
  );
}
