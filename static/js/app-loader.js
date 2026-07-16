(function () {
    let started = false;

    function loadAppBundle() {
        if (started) return;
        started = true;
        const script = document.createElement('script');
        script.src = '/static/js/app.js?v=20260716-fit392-rpe';
        script.async = true;
        document.body.appendChild(script);
    }

    if (document.readyState === 'complete') {
        window.setTimeout(loadAppBundle, 0);
    } else {
        window.addEventListener('load', loadAppBundle, { once: true });
    }
})();
