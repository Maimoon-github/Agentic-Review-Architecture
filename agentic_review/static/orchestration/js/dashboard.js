/**
 * Dashboard Logic
 */
document.addEventListener('DOMContentLoaded', () => {
    // Refresh for active runs
    const hasRunning = document.querySelector('.badge-RUNNING');
    if (hasRunning) {
        setTimeout(() => location.reload(), 5000);
    }
});
