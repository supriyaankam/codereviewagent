import { diffLines } from 'diff';

function splitChunkLines(value) {
  const lines = value.split('\n');
  if (lines.at(-1) === '') lines.pop();
  return lines;
}

export function buildComparisonRows(original, proposed, startLine = 1) {
  const rows = [];
  let originalLine = startLine;
  let proposedLine = startLine;
  let removed = [];
  let added = [];

  function flushChanges() {
    const count = Math.max(removed.length, added.length);
    for (let index = 0; index < count; index += 1) {
      const oldLine = removed[index];
      const newLine = added[index];
      rows.push({
        kind: oldLine !== undefined && newLine !== undefined
          ? 'modified'
          : oldLine !== undefined ? 'removed' : 'added',
        original: oldLine,
        proposed: newLine,
        originalNumber: oldLine !== undefined ? originalLine++ : null,
        proposedNumber: newLine !== undefined ? proposedLine++ : null,
      });
    }
    removed = [];
    added = [];
  }

  for (const part of diffLines(original, proposed)) {
    if (part.removed || part.added) {
      const lines = splitChunkLines(part.value);
      if (part.removed) removed.push(...lines);
      else added.push(...lines);
      continue;
    }

    flushChanges();
    for (const line of splitChunkLines(part.value)) {
      rows.push({
        kind: 'unchanged',
        original: line,
        proposed: line,
        originalNumber: originalLine++,
        proposedNumber: proposedLine++,
      });
    }
  }
  flushChanges();
  return rows;
}
