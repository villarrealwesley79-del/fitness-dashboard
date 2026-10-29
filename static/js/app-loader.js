(function () {
    let started = false;

    function loadAppBundle() {
        if (started) return;
        started = true;
        const script = document.createElement('script');
        script.src = '/static/js/app.js?v=20261028-w4c-apple-via-ow';
        script.async = true;
        document.body.appendChild(script);
    }

    if (document.readyState !== 'loading') {
        window.setTimeout(loadAppBundle, 0);
    } else {
        document.addEventListener('DOMContentLoaded', loadAppBundle, { once: true });
    }
})();
