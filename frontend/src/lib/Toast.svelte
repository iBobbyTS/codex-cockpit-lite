<script>
  let {
    message,
    tone = 'error',
    durationMs = 10000,
    onDismiss,
    closeLabel = '关闭提示',
  } = $props();

  $effect(() => {
    if (!message || durationMs <= 0) return;
    const timer = setTimeout(onDismiss, durationMs);
    return () => clearTimeout(timer);
  });
</script>

<div class="toast {tone}" role={tone === 'error' ? 'alert' : 'status'} aria-live="polite">
  {message}
  <button class="toast-close" aria-label={closeLabel} onclick={onDismiss}>✕</button>
</div>

<style>
  .toast {
    position: fixed;
    top: 16px;
    left: 50%;
    z-index: 200;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px;
    border-radius: 8px;
    color: white;
    transform: translateX(-50%);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
  }
  .toast.error {
    background: var(--danger);
  }
  .toast.success {
    background: var(--success);
  }
  .toast-close {
    padding: 0 2px;
    border: none;
    background: none;
    color: white;
  }
</style>
