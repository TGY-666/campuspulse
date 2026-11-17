/* 登录页背景：Vanta NET（three.js + vanta.net，MIT，static/vendor/vanta/）。
   首选 WebGL 动效；减少动效、WebGL 不可用或初始化失败时，退回静态帧
   static/cp/login-net.jpg。全部资源在本地，运行时不请求外部域名。 */
(function () {
    var host = document.getElementById('loginBg');
    if (!host) return;

    function staticFrame() {
        document.documentElement.classList.add('login-static');
    }

    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
        staticFrame();
        return;
    }

    var probe = document.createElement('canvas');
    var gl = probe.getContext('webgl') || probe.getContext('experimental-webgl');
    if (!gl) {
        staticFrame();
        return;
    }
    var lose = gl.getExtension('WEBGL_lose_context');
    if (lose) lose.loseContext();

    if (typeof window.THREE === 'undefined' || !window.VANTA || typeof window.VANTA.NET !== 'function') {
        staticFrame();
        return;
    }

    var effect;
    try {
        effect = window.VANTA.NET({
            el: '#loginBg',
            mouseControls: false,
            touchControls: false,
            gyroControls: false,
            minHeight: 200,
            minWidth: 200,
            scale: 1,
            scaleMobile: 1,
            color: 0x9bbcff,
            backgroundColor: 0x070f1e,
            points: 10,
            maxDistance: 22,
            spacing: 18,
            showDots: true
        });
    } catch (e) {
        staticFrame();
        return;
    }

    var canvas = host.querySelector('canvas');
    if (canvas) {
        canvas.addEventListener('webglcontextlost', function (event) {
            event.preventDefault();
            if (effect && typeof effect.destroy === 'function') effect.destroy();
            staticFrame();
        });
    }
})();
