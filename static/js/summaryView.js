document.addEventListener('DOMContentLoaded', () => {
    const modelSelect = document.getElementById('summaryModelSelect');
    if (!modelSelect) {
        return;
    }

    modelSelect.addEventListener('change', () => {
        const url = new URL(window.location.href);
        url.searchParams.set('model', modelSelect.value);
        url.searchParams.delete('run');
        window.location.href = url.toString();
    });
});
