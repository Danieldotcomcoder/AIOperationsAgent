/**
 * State, shown as icon + label. Color is a third, redundant channel: a reader who
 * cannot tell the colors apart still gets the glyph and the words.
 */

import { words } from '../lib/format';

type Tone = 'good' | 'warning' | 'serious' | 'critical' | 'info' | 'neutral';

const GLYPH: Record<Tone, string> = {
  good: '✓',
  warning: '!',
  serious: '?',
  critical: '✕',
  info: '•',
  neutral: '–',
};

/** Run, proposal and document states mapped once, so every screen agrees. */
const TONES: Record<string, Tone> = {
  // runs
  queued: 'info',
  investigating: 'info',
  needs_review: 'warning',
  completed: 'good',
  cancelled: 'neutral',
  // proposals
  pending: 'warning',
  approved: 'info',
  executing: 'info',
  succeeded: 'good',
  rejected: 'neutral',
  expired: 'neutral',
  reconciliation_required: 'serious',
  // documents
  indexing: 'info',
  active: 'good',
  deleted: 'neutral',
  // shared
  failed: 'critical',
};

const BUSY = new Set(['queued', 'investigating', 'approved', 'executing', 'indexing']);

const LABELS: Record<string, string> = {
  needs_review: 'awaiting approval',
  reconciliation_required: 'outcome uncertain',
};

export function Status({ value, label }: { value: string; label?: string }) {
  const tone = TONES[value] ?? 'neutral';
  return (
    <span className={`status ${tone}${BUSY.has(value) ? ' busy' : ''}`}>
      <span className="icon" aria-hidden="true">
        {GLYPH[tone]}
      </span>
      {label ?? LABELS[value] ?? words(value)}
    </span>
  );
}

export function Banner({
  tone,
  title,
  children,
}: {
  tone: 'info' | 'warning' | 'critical' | 'good';
  title?: string;
  children: React.ReactNode;
}) {
  const glyph = { info: 'i', warning: '!', critical: '✕', good: '✓' }[tone];
  return (
    <div className={`banner ${tone}`} role={tone === 'critical' ? 'alert' : 'status'}>
      <span className="glyph" aria-hidden="true">
        {glyph}
      </span>
      <div>
        {title ? <strong>{title} </strong> : null}
        {children}
      </div>
    </div>
  );
}
