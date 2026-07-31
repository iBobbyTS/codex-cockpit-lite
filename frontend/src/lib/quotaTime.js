function padMinutes(value) {
  return String(value).padStart(2, '0');
}

export function formatQuotaReset(resetAtSeconds, nowMs = Date.now()) {
  const resetAt = Number(resetAtSeconds);
  if (!Number.isFinite(resetAt) || resetAt <= 0) return '-- (--)';

  const resetMs = resetAt * 1000;
  const remainingMinutes = Math.max(0, Math.ceil((resetMs - nowMs) / 60_000));
  const days = Math.floor(remainingMinutes / (24 * 60));
  const hours = Math.floor((remainingMinutes % (24 * 60)) / 60);
  const minutes = remainingMinutes % 60;

  const resetDate = new Date(resetMs);
  const absolute = `${resetDate.getMonth() + 1}/${resetDate.getDate()} ${resetDate.getHours()}:${padMinutes(resetDate.getMinutes())}`;

  return `${days}d ${hours}:${padMinutes(minutes)} (${absolute})`;
}
