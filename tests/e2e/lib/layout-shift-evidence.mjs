// Delivered observer entries must be retained alongside pending takeRecords().
// Clearing the callback's array before measurement silently erases real CLS.
export function cumulativeLayoutShift(entries) {
  let maximum = 0, sum = 0, first = 0, last = -Infinity;
  for (const entry of [...entries].sort((a, b) => a.startTime - b.startTime)) {
    if (entry.hadRecentInput) continue;
    if (!Number.isFinite(entry.value) || entry.value < 0 || !Number.isFinite(entry.startTime))
      throw new Error('Invalid layout-shift evidence');
    if (!sum || entry.startTime - last >= 1000 || entry.startTime - first >= 5000) {
      sum = 0;
      first = entry.startTime;
    }
    sum += entry.value;
    last = entry.startTime;
    maximum = Math.max(maximum, sum);
  }
  return maximum;
}
