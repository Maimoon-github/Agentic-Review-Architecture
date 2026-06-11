/**
 * Pipeline Creation Logic
 */
document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('pipeline-form');
    if (form) {
        form.addEventListener('submit', function () {
            const btnText = document.getElementById('btn-text');
            const btnLoading = document.getElementById('btn-loading');
            const submitBtn = document.getElementById('submit-btn');

            if (btnText) btnText.style.display = 'none';
            if (btnLoading) {
                btnLoading.style.display = 'flex';
                btnLoading.style.alignItems = 'center';
                btnLoading.style.gap = '8px';
            }
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.style.opacity = '0.7';
            }
        });
    }
});
