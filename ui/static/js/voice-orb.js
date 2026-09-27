/* ANAM GUIDE: VOICE CALL ORB ANIMATION
   What: The pretty full-screen glowing orb with drifting particles shown during voice calls — pure visuals, with phases for idle/listening/thinking/speaking.
   Loaded by: static/index.html (main chat page); chat.js switches its phase as a call moves along.
   Edit here when: You want to change the orb's colors, particle look, or how each call phase animates. No audio logic lives here. */

/**
 * Voice Orb — Full-screen cosmic particle visualization
 * Phases: idle, listening, thinking, speaking
 */
const VoiceOrb = (() => {
    let canvas, ctx, raf;
    let phase = 'idle';
    let particles = [];
    let coreRadius = 0;
    let targetCoreRadius = 40;
    let coreGlow = 0;
    let time = 0;
    let W = 0, H = 0, cx = 0, cy = 0;

    const PARTICLE_COUNT = 80;
    const COLORS = {
        gold: [255, 214, 150],
        pink: [255, 183, 212],
        white: [255, 246, 235],
        violet: [200, 170, 255],
    };
    const COLOR_KEYS = Object.keys(COLORS);

    function init(canvasEl) {
        canvas = canvasEl;
        ctx = canvas.getContext('2d');
        resize();
        seedParticles();
        window.addEventListener('resize', resize);
    }

    function resize() {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = canvas.clientWidth;
        H = canvas.clientHeight;
        canvas.width = W * dpr;
        canvas.height = H * dpr;
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        cx = W / 2;
        cy = H / 2;
    }

    function seedParticles() {
        particles = [];
        for (let i = 0; i < PARTICLE_COUNT; i++) {
            particles.push(createParticle(i));
        }
    }

    function createParticle(i) {
        const angle = Math.random() * Math.PI * 2;
        const dist = 60 + Math.random() * Math.min(W, H) * 0.35;
        const colorKey = COLOR_KEYS[i % COLOR_KEYS.length];
        const rgb = COLORS[colorKey];
        return {
            x: cx + Math.cos(angle) * dist,
            y: cy + Math.sin(angle) * dist,
            vx: (Math.random() - 0.5) * 0.3,
            vy: (Math.random() - 0.5) * 0.3,
            baseR: 1.5 + Math.random() * 3,
            r: 2,
            alpha: 0.3 + Math.random() * 0.5,
            rgb,
            orbitSpeed: (0.2 + Math.random() * 0.5) * (Math.random() < 0.5 ? 1 : -1),
            orbitDist: dist,
            angle,
            drift: Math.random() * Math.PI * 2,
            pulseSpeed: 0.5 + Math.random() * 1.5,
            layer: Math.random(), // depth layer for parallax
        };
    }

    function setPhase(newPhase) {
        phase = newPhase;
    }

    function start() {
        if (raf) return;
        time = performance.now();
        loop();
    }

    function stop() {
        if (raf) {
            cancelAnimationFrame(raf);
            raf = null;
        }
    }

    function loop() {
        const now = performance.now();
        const dt = Math.min((now - time) / 1000, 0.05);
        time = now;
        update(dt);
        draw();
        raf = requestAnimationFrame(loop);
    }

    function update(dt) {
        const t = time * 0.001;

        // Core animation
        switch (phase) {
            case 'idle':
                targetCoreRadius = 32 + Math.sin(t * 0.8) * 6;
                coreGlow = 0.3 + Math.sin(t * 0.5) * 0.1;
                break;
            case 'listening':
                targetCoreRadius = 38 + Math.sin(t * 1.4) * 10;
                coreGlow = 0.5 + Math.sin(t * 1.2) * 0.2;
                break;
            case 'thinking':
                targetCoreRadius = 28 + Math.sin(t * 0.6) * 4;
                coreGlow = 0.6 + Math.sin(t * 2.0) * 0.15;
                break;
            case 'speaking':
                targetCoreRadius = 42 + Math.sin(t * 3.0) * 14;
                coreGlow = 0.7 + Math.sin(t * 2.5) * 0.25;
                break;
        }
        coreRadius += (targetCoreRadius - coreRadius) * dt * 5;

        // Particle behavior by phase
        for (const p of particles) {
            const dx = p.x - cx;
            const dy = p.y - cy;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const ang = Math.atan2(dy, dx);

            switch (phase) {
                case 'idle': {
                    // Gentle cosmic drift — slow orbit + slight wobble
                    p.angle += p.orbitSpeed * 0.15 * dt;
                    const targetD = p.orbitDist + Math.sin(t * 0.3 + p.drift) * 20;
                    const tx = cx + Math.cos(p.angle) * targetD;
                    const ty = cy + Math.sin(p.angle) * targetD;
                    p.vx += (tx - p.x) * 0.4 * dt;
                    p.vy += (ty - p.y) * 0.4 * dt;
                    p.r = p.baseR * (0.8 + Math.sin(t * p.pulseSpeed + p.drift) * 0.2);
                    p.alpha = 0.25 + Math.sin(t * 0.5 + p.drift) * 0.15;
                    break;
                }
                case 'listening': {
                    // Particles draw inward, pulsing with "breath"
                    p.angle += p.orbitSpeed * 0.3 * dt;
                    const breathe = Math.sin(t * 1.4) * 0.3;
                    const targetD = p.orbitDist * (0.55 + breathe * 0.15) + Math.sin(t + p.drift) * 15;
                    const tx = cx + Math.cos(p.angle) * targetD;
                    const ty = cy + Math.sin(p.angle) * targetD;
                    p.vx += (tx - p.x) * 1.0 * dt;
                    p.vy += (ty - p.y) * 1.0 * dt;
                    p.r = p.baseR * (1.0 + Math.sin(t * 1.6 + p.drift) * 0.3);
                    p.alpha = 0.4 + Math.sin(t * 1.4 + p.drift) * 0.2;
                    break;
                }
                case 'thinking': {
                    // Mesmerizing spiral — particles orbit faster, tighter
                    p.angle += p.orbitSpeed * 0.8 * dt;
                    const spiral = Math.sin(t * 0.4 + p.drift * 2) * 30;
                    const targetD = p.orbitDist * 0.5 + spiral;
                    const tx = cx + Math.cos(p.angle) * targetD;
                    const ty = cy + Math.sin(p.angle) * targetD;
                    p.vx += (tx - p.x) * 1.5 * dt;
                    p.vy += (ty - p.y) * 1.5 * dt;
                    p.r = p.baseR * (0.7 + Math.sin(t * 2.0 + p.drift) * 0.4);
                    p.alpha = 0.3 + Math.sin(t * 1.8 + p.drift) * 0.25;
                    break;
                }
                case 'speaking': {
                    // Particles pulse outward in waves from center
                    p.angle += p.orbitSpeed * 0.4 * dt;
                    const wave = Math.sin(t * 3.0 - dist * 0.02) * 40;
                    const targetD = p.orbitDist * 0.7 + wave;
                    const tx = cx + Math.cos(p.angle) * targetD;
                    const ty = cy + Math.sin(p.angle) * targetD;
                    p.vx += (tx - p.x) * 1.2 * dt;
                    p.vy += (ty - p.y) * 1.2 * dt;
                    p.r = p.baseR * (1.1 + Math.sin(t * 2.8 + p.drift) * 0.4);
                    p.alpha = 0.5 + Math.sin(t * 2.5 + p.drift) * 0.3;
                    break;
                }
            }

            // Apply velocity with damping
            p.vx *= 0.92;
            p.vy *= 0.92;
            p.x += p.vx;
            p.y += p.vy;

            // Clamp alpha
            p.alpha = Math.max(0, Math.min(1, p.alpha));
        }
    }

    function draw() {
        ctx.clearRect(0, 0, W, H);

        // Background cosmic glow
        const bgGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(W, H) * 0.6);
        const glowAlpha = coreGlow * 0.12;
        bgGrad.addColorStop(0, `rgba(255, 214, 150, ${glowAlpha})`);
        bgGrad.addColorStop(0.3, `rgba(255, 183, 212, ${glowAlpha * 0.6})`);
        bgGrad.addColorStop(0.6, `rgba(200, 170, 255, ${glowAlpha * 0.3})`);
        bgGrad.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = bgGrad;
        ctx.fillRect(0, 0, W, H);

        // Draw particles (back layer first)
        const sorted = particles.slice().sort((a, b) => a.layer - b.layer);
        for (const p of sorted) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, Math.max(0.5, p.r), 0, Math.PI * 2);
            ctx.fillStyle = `rgba(${p.rgb[0]}, ${p.rgb[1]}, ${p.rgb[2]}, ${p.alpha})`;
            ctx.fill();

            // Glow for larger particles
            if (p.r > 2.5) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r * 3, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(${p.rgb[0]}, ${p.rgb[1]}, ${p.rgb[2]}, ${p.alpha * 0.12})`;
                ctx.fill();
            }
        }

        // Draw connecting lines between nearby particles
        ctx.lineWidth = 0.5;
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const a = particles[i], b = particles[j];
                const dx = a.x - b.x, dy = a.y - b.y;
                const d2 = dx * dx + dy * dy;
                const maxD = phase === 'thinking' ? 5000 : 3600;
                if (d2 < maxD) {
                    const alpha = (1 - d2 / maxD) * 0.08;
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.strokeStyle = `rgba(255, 230, 210, ${alpha})`;
                    ctx.stroke();
                }
            }
        }

        // Core orb
        const r = coreRadius;
        const coreGrad = ctx.createRadialGradient(cx - r * 0.2, cy - r * 0.2, 0, cx, cy, r);
        coreGrad.addColorStop(0, `rgba(255, 248, 235, ${0.85 + coreGlow * 0.15})`);
        coreGrad.addColorStop(0.35, `rgba(255, 218, 162, ${0.75 + coreGlow * 0.15})`);
        coreGrad.addColorStop(0.65, `rgba(255, 186, 212, ${0.6 + coreGlow * 0.1})`);
        coreGrad.addColorStop(1, 'rgba(200, 160, 220, 0)');
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fillStyle = coreGrad;
        ctx.fill();

        // Core outer glow
        const glowGrad = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r * 2.5);
        glowGrad.addColorStop(0, `rgba(255, 209, 144, ${coreGlow * 0.4})`);
        glowGrad.addColorStop(0.5, `rgba(255, 184, 212, ${coreGlow * 0.2})`);
        glowGrad.addColorStop(1, 'rgba(200, 170, 255, 0)');
        ctx.beginPath();
        ctx.arc(cx, cy, r * 2.5, 0, Math.PI * 2);
        ctx.fillStyle = glowGrad;
        ctx.fill();

        // Rings
        const t = time * 0.001;
        const ringAlpha = phase === 'thinking' ? 0.2 + Math.sin(t * 2) * 0.1 : 0.1;
        for (let i = 1; i <= 3; i++) {
            const rr = r + 20 * i + Math.sin(t * (0.5 + i * 0.3)) * 8;
            ctx.beginPath();
            ctx.arc(cx, cy, rr, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(255, 225, 190, ${ringAlpha / i})`;
            ctx.lineWidth = 1;
            ctx.stroke();
        }
    }

    function destroy() {
        stop();
        window.removeEventListener('resize', resize);
        particles = [];
    }

    return { init, start, stop, setPhase, destroy, resize };
})();
