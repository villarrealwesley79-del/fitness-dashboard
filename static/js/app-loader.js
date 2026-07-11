(function () {
    let started = false;

    function loadAppBundle() {
        if (started) return;
        started = true;
        const script = document.createElement('script');
        script.src = '/static/js/app.js?v=20260629-fit253-open-wearables-link';
        script.async = true;
        document.body.appendChild(script);
    }

    if (document.readyState !== 'loading') {
        window.setTimeout(loadAppBundle, 0);
    } else {
        document.addEventListener('DOMContentLoaded', loadAppBundle, { once: true });
    }
})();
