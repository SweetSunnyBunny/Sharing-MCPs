/* ANAM GUIDE: PACK EMBODIMENT LAB BEHAVIOR */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';


const $ = (id) => document.getElementById(id);
const normalizeControlName = (value) => String(value || '').toLowerCase().replace(/[^a-z0-9]/g, '');

const VISEME_ALIASES = {
    sil: ['viseme_sil', 'visemeSil', 'v_sil'],
    PP: ['viseme_PP', 'visemePP', 'v_pp'],
    FF: ['viseme_FF', 'visemeFF', 'v_ff'],
    TH: ['viseme_TH', 'visemeTH', 'v_th'],
    DD: ['viseme_DD', 'visemeDD', 'v_dd'],
    kk: ['viseme_kk', 'visemeKK', 'v_kk'],
    CH: ['viseme_CH', 'visemeCH', 'v_ch'],
    SS: ['viseme_SS', 'visemeSS', 'v_ss'],
    nn: ['viseme_nn', 'visemeNN', 'v_nn'],
    RR: ['viseme_RR', 'visemeRR', 'v_rr'],
    aa: ['viseme_aa', 'visemeAA', 'v_aa'],
    E: ['viseme_E', 'visemeE', 'v_e'],
    I: ['viseme_I', 'visemeI', 'v_i'],
    O: ['viseme_O', 'visemeO', 'v_o'],
    U: ['viseme_U', 'visemeU', 'v_u'],
};

async function getJson(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `${response.status} ${response.statusText}`);
    }
    return response.json();
}

function setConnection(state, text) {
    const pill = $('connection-pill');
    pill.dataset.state = state;
    pill.textContent = text;
}

function appendLog(kind, text) {
    const line = document.createElement('p');
    line.className = `log-${kind}`;
    line.textContent = text;
    $('call-log').appendChild(line);
    $('call-log').scrollTop = $('call-log').scrollHeight;
}


class AvatarStage {
    constructor(canvas) {
        this.canvas = canvas;
        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(32, 1, 0.01, 1000);
        this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.08;
        this.renderer.shadowMap.enabled = true;
        this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

        this.controls = new OrbitControls(this.camera, canvas);
        this.controls.enableDamping = true;
        this.controls.enablePan = false;
        this.controls.minDistance = 0.4;
        this.controls.maxDistance = 30;

        this.clock = new THREE.Clock();
        this.loader = new GLTFLoader();
        this.model = null;
        this.modelHeight = 1;
        this.modelBaseY = 0;
        this.phase = 'idle';
        this.speakingLevel = 0;
        this.mixer = null;
        this.morphChannels = new Map();
        this.drivenMorphChannels = [];
        this.jawBone = null;
        this.jawBase = new THREE.Quaternion();
        this._jawDelta = new THREE.Quaternion();
        this._jawAxis = new THREE.Vector3(1, 0, 0);
        this.headBone = null;
        this.headBase = new THREE.Quaternion();
        this.neckBone = null;
        this.neckBase = new THREE.Quaternion();
        this.leftEyeBone = null;
        this.leftEyeBase = new THREE.Quaternion();
        this.rightEyeBone = null;
        this.rightEyeBase = new THREE.Quaternion();
        this.speechCues = [];
        this.speechTime = 0;
        this.speechDuration = 0;
        this.blinkStarted = null;
        this.nextBlinkAt = 1.8 + Math.random() * 2.4;

        this.scene.add(new THREE.HemisphereLight(0xf7e9dc, 0x151627, 2.35));
        const key = new THREE.DirectionalLight(0xffdfbf, 4.2);
        key.position.set(3.5, 6, 4.5);
        key.castShadow = true;
        key.shadow.mapSize.set(2048, 2048);
        key.shadow.camera.near = 0.1;
        key.shadow.camera.far = 30;
        this.scene.add(key);
        const rim = new THREE.DirectionalLight(0x7998ff, 2.2);
        rim.position.set(-4, 3, -4);
        this.scene.add(rim);

        this.floor = new THREE.Mesh(
            new THREE.CircleGeometry(2.4, 96),
            new THREE.MeshStandardMaterial({
                color: 0x241e2d,
                roughness: 0.92,
                metalness: 0,
                transparent: true,
                opacity: 0.78,
            }),
        );
        this.floor.rotation.x = -Math.PI / 2;
        this.floor.receiveShadow = true;
        this.scene.add(this.floor);

        this.halo = new THREE.Mesh(
            new THREE.RingGeometry(1.15, 1.2, 96),
            new THREE.MeshBasicMaterial({
                color: 0xd2a25b,
                transparent: true,
                opacity: 0.14,
                side: THREE.DoubleSide,
            }),
        );
        this.halo.rotation.x = -Math.PI / 2;
        this.halo.position.y = 0.008;
        this.scene.add(this.halo);

        this._resizeObserver = new ResizeObserver(() => this.resize());
        this._resizeObserver.observe(canvas.parentElement);
        this.resize();
        this.animate();
    }

    resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(1, Math.round(rect.height));
        this.renderer.setSize(width, height, false);
        this.camera.aspect = width / height;
        this.camera.updateProjectionMatrix();
    }

    disposeModel() {
        if (!this.model) return;
        this.scene.remove(this.model);
        this.model.traverse((object) => {
            if (object.geometry) object.geometry.dispose();
            const materials = Array.isArray(object.material) ? object.material : [object.material];
            materials.filter(Boolean).forEach((material) => {
                Object.values(material).forEach((value) => {
                    if (value && value.isTexture) value.dispose();
                });
                material.dispose();
            });
        });
        this.model = null;
        this.mixer = null;
        this.morphChannels.clear();
        this.drivenMorphChannels = [];
        this.jawBone = null;
        this.headBone = null;
        this.neckBone = null;
        this.leftEyeBone = null;
        this.rightEyeBone = null;
        this.speechCues = [];
    }

    async load(url) {
        $('stage-loading').classList.remove('hidden');
        $('stage-loading').textContent = 'Bringing the body through…';
        this.disposeModel();
        try {
            const gltf = await this.loader.loadAsync(url);
            this.model = gltf.scene;
            this.scene.add(this.model);

            this.model.traverse((object) => {
                if (object.isMesh || object.isSkinnedMesh) {
                    object.castShadow = true;
                    object.receiveShadow = true;
                }
            });

            const initialBox = new THREE.Box3().setFromObject(this.model);
            const center = initialBox.getCenter(new THREE.Vector3());
            this.model.position.x -= center.x;
            this.model.position.z -= center.z;
            this.model.position.y -= initialBox.min.y;

            const box = new THREE.Box3().setFromObject(this.model);
            const size = box.getSize(new THREE.Vector3());
            this.modelHeight = Math.max(size.y, 0.01);
            this.modelBaseY = this.model.position.y;
            this.floor.scale.setScalar(Math.max(size.x, size.z, this.modelHeight * 0.34));
            this.halo.scale.setScalar(Math.max(size.x, size.z, this.modelHeight * 0.32));

            this.camera.near = Math.max(this.modelHeight / 1000, 0.01);
            this.camera.far = Math.max(this.modelHeight * 30, 100);
            this.camera.position.set(
                this.modelHeight * 0.56,
                this.modelHeight * 0.46,
                this.modelHeight * 1.48,
            );
            this.controls.target.set(0, this.modelHeight * 0.48, 0);
            this.controls.minDistance = this.modelHeight * 0.38;
            this.controls.maxDistance = this.modelHeight * 4;
            this.camera.updateProjectionMatrix();
            this.controls.update();

            this._collectPerformanceControls();
            if (gltf.animations.length) {
                this.mixer = new THREE.AnimationMixer(this.model);
                const idle = gltf.animations.find((clip) => /idle/i.test(clip.name)) || gltf.animations[0];
                this.mixer.clipAction(idle).play();
            }

            $('stage-loading').classList.add('hidden');
        } catch (error) {
            $('stage-loading').textContent = `Body load failed: ${error.message}`;
            throw error;
        }
    }

    _collectPerformanceControls() {
        this.model.traverse((object) => {
            if (object.morphTargetDictionary && object.morphTargetInfluences) {
                for (const [name, index] of Object.entries(object.morphTargetDictionary)) {
                    const normalized = normalizeControlName(name);
                    const channel = { object, index, name };
                    if (!this.morphChannels.has(normalized)) this.morphChannels.set(normalized, []);
                    this.morphChannels.get(normalized).push(channel);
                    if (/^(eye|mouth|jaw|brow|cheek|nose|viseme|v)/.test(normalized)) {
                        this.drivenMorphChannels.push(channel);
                    }
                }
            }
            const loweredName = normalizeControlName(object.name);
            if (!object.isBone) return;
            if (!this.jawBone && loweredName.includes('jaw')) {
                this.jawBone = object;
                this.jawBase.copy(object.quaternion);
            }
            if (!this.headBone && loweredName === 'head') {
                this.headBone = object;
                this.headBase.copy(object.quaternion);
            }
            if (!this.neckBone && loweredName.includes('neck')) {
                this.neckBone = object;
                this.neckBase.copy(object.quaternion);
            }
            if (!this.leftEyeBone && /^(lefteye|eyel|leye)$/.test(loweredName)) {
                this.leftEyeBone = object;
                this.leftEyeBase.copy(object.quaternion);
            }
            if (!this.rightEyeBone && /^(righteye|eyer|reye)$/.test(loweredName)) {
                this.rightEyeBone = object;
                this.rightEyeBase.copy(object.quaternion);
            }
        });
    }

    _buildSpeechCues(text) {
        const source = String(text || '').toLowerCase();
        const cues = [];
        const push = (cue) => {
            if (cues[cues.length - 1] !== cue) cues.push(cue);
        };
        for (let index = 0; index < source.length;) {
            const pair = source.slice(index, index + 2);
            const letter = source[index];
            if (pair === 'th') { push('TH'); index += 2; continue; }
            if (pair === 'ch' || pair === 'sh') { push('CH'); index += 2; continue; }
            if (/[bmp]/.test(letter)) push('PP');
            else if (/[fv]/.test(letter)) push('FF');
            else if (/[td]/.test(letter)) push('DD');
            else if (/[kg]/.test(letter)) push('kk');
            else if (/[czsx]/.test(letter)) push('SS');
            else if (/[nl]/.test(letter)) push('nn');
            else if (letter === 'r') push('RR');
            else if (/[a]/.test(letter)) push('aa');
            else if (/[e]/.test(letter)) push('E');
            else if (/[iy]/.test(letter)) push('I');
            else if (/[o]/.test(letter)) push('O');
            else if (/[uwq]/.test(letter)) push('U');
            else if (/\s|[,.!?;:—-]/.test(letter)) push('sil');
            index += 1;
        }
        return cues.length ? cues : ['aa'];
    }

    beginSpeech(text) {
        this.speechCues = this._buildSpeechCues(text);
        this.speechTime = 0;
        this.speechDuration = 0;
    }

    updateSpeechTiming(currentTime, duration) {
        this.speechTime = Number.isFinite(currentTime) ? currentTime : 0;
        this.speechDuration = Number.isFinite(duration) && duration > 0 ? duration : 0;
    }

    endSpeech() {
        this.speechCues = [];
        this.speechTime = 0;
        this.speechDuration = 0;
        this.setSpeakingLevel(0);
    }

    setPhase(phase) {
        this.phase = phase || 'idle';
        $('stage-phase').textContent = this.phase;
    }

    setSpeakingLevel(level) {
        this.speakingLevel = THREE.MathUtils.clamp(level || 0, 0, 1);
    }

    _setMorphTarget(targets, aliases, value) {
        for (const alias of aliases) {
            for (const channel of this.morphChannels.get(normalizeControlName(alias)) || []) {
                targets.set(channel, Math.max(targets.get(channel) || 0, value));
            }
        }
    }

    _blinkValue(elapsed) {
        if (this.blinkStarted === null && elapsed >= this.nextBlinkAt) this.blinkStarted = elapsed;
        if (this.blinkStarted === null) return 0;
        const progress = (elapsed - this.blinkStarted) / 0.17;
        if (progress >= 1) {
            this.blinkStarted = null;
            this.nextBlinkAt = elapsed + 2.2 + Math.random() * 3.8;
            return 0;
        }
        return Math.sin(progress * Math.PI);
    }

    _currentViseme() {
        if (!this.speechCues.length || !this.speechDuration) return 'aa';
        const progress = THREE.MathUtils.clamp(this.speechTime / this.speechDuration, 0, 0.9999);
        return this.speechCues[Math.floor(progress * this.speechCues.length)] || 'aa';
    }

    _driveAttention(elapsed) {
        const listening = this.phase === 'listening' ? 1 : 0;
        const speaking = this.phase === 'speaking' ? 1 : 0;
        const yaw = Math.sin(elapsed * 0.31) * 0.018 + listening * 0.012;
        const pitch = Math.sin(elapsed * 0.23 + 1.1) * 0.009 - speaking * 0.007;
        const roll = Math.sin(elapsed * 0.19 + 0.7) * 0.006;
        if (this.headBone) {
            const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch, yaw, roll));
            this.headBone.quaternion.copy(this.headBase).multiply(delta);
        }
        if (this.neckBone) {
            const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch * 0.35, yaw * 0.3, 0));
            this.neckBone.quaternion.copy(this.neckBase).multiply(delta);
        }
        const eyeYaw = Math.sin(elapsed * 0.37 + 0.4) * 0.055;
        const eyePitch = Math.sin(elapsed * 0.29) * 0.025;
        for (const [bone, base] of [[this.leftEyeBone, this.leftEyeBase], [this.rightEyeBone, this.rightEyeBase]]) {
            if (!bone) continue;
            const delta = new THREE.Quaternion().setFromEuler(new THREE.Euler(eyePitch, eyeYaw, 0));
            bone.quaternion.copy(base).multiply(delta);
        }
    }

    _driveFace(elapsed) {
        const targets = new Map();
        const blink = this._blinkValue(elapsed);
        this._setMorphTarget(targets, ['eyeBlinkLeft', 'blinkLeft'], blink);
        this._setMorphTarget(targets, ['eyeBlinkRight', 'blinkRight'], blink);

        const gazeX = Math.sin(elapsed * 0.37 + 0.4) * 0.16;
        const gazeY = Math.sin(elapsed * 0.29) * 0.08;
        this._setMorphTarget(targets, ['eyeLookOutLeft', 'eyeLookInRight'], Math.max(0, gazeX));
        this._setMorphTarget(targets, ['eyeLookInLeft', 'eyeLookOutRight'], Math.max(0, -gazeX));
        this._setMorphTarget(targets, ['eyeLookUpLeft', 'eyeLookUpRight'], Math.max(0, gazeY));
        this._setMorphTarget(targets, ['eyeLookDownLeft', 'eyeLookDownRight'], Math.max(0, -gazeY));

        const speaking = this.phase === 'speaking';
        const level = speaking ? Math.max(0.035, this.speakingLevel) : 0;
        const viseme = this._currentViseme();
        const jawScale = viseme === 'PP' || viseme === 'sil' ? 0.14 : 0.72;
        this._setMorphTarget(targets, ['jawOpen', 'mouthOpen'], level * jawScale);
        if (speaking && viseme !== 'sil') {
            this._setMorphTarget(targets, VISEME_ALIASES[viseme] || VISEME_ALIASES.aa, Math.max(0.2, level));
        }
        const engaged = this.phase === 'listening' ? 0.08 : speaking ? 0.045 : 0.025;
        this._setMorphTarget(targets, ['browInnerUp'], engaged);
        this._setMorphTarget(targets, ['mouthSmileLeft', 'mouthSmileRight'], speaking ? 0.035 : 0.018);

        for (const channel of this.drivenMorphChannels) {
            const current = channel.object.morphTargetInfluences[channel.index] || 0;
            const target = targets.get(channel) || 0;
            channel.object.morphTargetInfluences[channel.index] = THREE.MathUtils.lerp(current, target, 0.34);
        }
        if (this.jawBone) {
            this._jawDelta.setFromAxisAngle(this._jawAxis, level * jawScale * 0.18);
            this.jawBone.quaternion.copy(this.jawBase).multiply(this._jawDelta);
        }
        this._driveAttention(elapsed);
    }

    animate() {
        requestAnimationFrame(() => this.animate());
        const delta = Math.min(this.clock.getDelta(), 0.05);
        const elapsed = this.clock.elapsedTime;
        if (this.mixer) this.mixer.update(delta);
        if (this.model) {
            const breath = Math.sin(elapsed * 1.45) * this.modelHeight * 0.0012;
            const speakingLift = this.phase === 'speaking'
                ? Math.sin(elapsed * 5.4) * this.modelHeight * 0.0007 * this.speakingLevel
                : 0;
            this.model.position.y = this.modelBaseY + breath + speakingLift;
            this._driveFace(elapsed);
        }
        const active = this.phase === 'speaking' ? this.speakingLevel : 0;
        this.halo.material.opacity = THREE.MathUtils.lerp(
            this.halo.material.opacity,
            0.11 + active * 0.42,
            0.18,
        );
        const haloPulse = 1 + Math.sin(elapsed * (active ? 5 : 1.5)) * (0.008 + active * 0.025);
        this.halo.scale.multiplyScalar(haloPulse / (this._lastHaloPulse || 1));
        this._lastHaloPulse = haloPulse;
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }
}


class NativeVoiceCall {
    constructor(stage) {
        this.stage = stage;
        this.ws = null;
        this.media = null;
        this.context = null;
        this.micAnalyser = null;
        this.speechAnalyser = null;
        this.recorder = null;
        this.recorderChunks = [];
        this.talking = false;
        this.talkStarted = 0;
        this.lastVoice = 0;
        this.playing = false;
        this.playQueue = [];
        this.currentAudio = null;
        this.frame = null;

        this.VAD_START_RMS = 0.028;
        this.VAD_KEEP_RMS = 0.014;
        this.END_SILENCE_MS = 700;
        this.MIN_UTTER_MS = 280;
        this.BARGE_RMS = 0.05;
    }

    async start(identity, tts) {
        this.media = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true },
        });
        this.context = new AudioContext();
        await this.context.resume();
        const source = this.context.createMediaStreamSource(this.media);
        this.micAnalyser = this.context.createAnalyser();
        this.micAnalyser.fftSize = 1024;
        source.connect(this.micAnalyser);
        this.speechAnalyser = this.context.createAnalyser();
        this.speechAnalyser.fftSize = 512;
        this.speechAnalyser.connect(this.context.destination);

        const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
        this.ws = new WebSocket(`${protocol}://${location.host}/api/voice/call/${encodeURIComponent(identity)}`);
        this.ws.onopen = () => {
            const hello = { type: 'hello' };
            if (tts) hello.tts = tts;
            this.ws.send(JSON.stringify(hello));
        };
        this.ws.onmessage = (event) => this._message(JSON.parse(event.data), identity);
        this.ws.onerror = () => {
            setConnection('error', 'call error');
            appendLog('system', 'The voice socket hit an error.');
        };
        this.ws.onclose = () => {
            if (this.context) appendLog('system', '— call ended —');
            this.cleanup(false);
        };

        this.frame = requestAnimationFrame(() => this._tick());
    }

    _message(message, identity) {
        if (message.type === 'ready') {
            setConnection('ready', `${message.identity} listening`);
            this.stage.setPhase('listening');
            $('start-call').disabled = true;
            $('end-call').disabled = false;
            $('identity-select').disabled = true;
            appendLog('system', `Connected — ${message.identity}, ${message.tts}.`);
        } else if (message.type === 'state') {
            this.stage.setPhase(message.phase);
            const state = message.phase === 'speaking' ? 'speaking' : message.phase === 'thinking' ? 'thinking' : 'ready';
            setConnection(state, `${identity} ${message.phase}`);
        } else if (message.type === 'transcript') {
            appendLog('you', `You: ${message.text}`);
        } else if (message.type === 'say') {
            if (message.text) appendLog('boy', `${identity}: ${message.text}`);
            if (message.audio) {
                this.playQueue.push(message);
                this._playNext();
            }
        } else if (message.type === 'rest') {
            appendLog('system', '— he chose to close the call —');
        } else if (message.type === 'error') {
            appendLog('system', `Error: ${message.error}`);
            setConnection('error', 'call error');
        }
    }

    _startRecorder() {
        this.recorderChunks = [];
        const preferred = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus'
            : 'audio/webm';
        this.recorder = new MediaRecorder(this.media, { mimeType: preferred });
        this.recorder.ondataavailable = (event) => {
            if (event.data.size) this.recorderChunks.push(event.data);
        };
        this.recorder.start();
    }

    async _shipUtterance() {
        if (!this.recorder) return;
        const recorder = this.recorder;
        const finished = new Promise((resolve) => { recorder.onstop = resolve; });
        recorder.stop();
        await finished;
        const blob = new Blob(this.recorderChunks, { type: recorder.mimeType || 'audio/webm' });
        this.recorder = null;
        this.recorderChunks = [];
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        const bytes = new Uint8Array(await blob.arrayBuffer());
        let binary = '';
        for (let index = 0; index < bytes.length; index += 0x8000) {
            binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 0x8000));
        }
        this.ws.send(JSON.stringify({ type: 'utterance', audio: btoa(binary), format: 'audio/webm' }));
    }

    _stopPlayback() {
        this.playQueue.length = 0;
        if (this.currentAudio) this.currentAudio.pause();
        this.currentAudio = null;
        this.playing = false;
        this.stage.endSpeech();
    }

    _playNext() {
        if (this.playing || !this.playQueue.length || !this.context) return;
        this.playing = true;
        const { audio, mime, text } = this.playQueue.shift();
        const element = new Audio(`data:${mime || 'audio/mpeg'};base64,${audio}`);
        this.currentAudio = element;
        this.stage.beginSpeech(text || '');
        const source = this.context.createMediaElementSource(element);
        source.connect(this.speechAnalyser);
        element.onended = () => {
            source.disconnect();
            this.playing = false;
            this.currentAudio = null;
            this.stage.endSpeech();
            this._playNext();
        };
        element.onerror = element.onended;
        element.play().catch((error) => {
            appendLog('system', `Audio playback failed: ${error.message}`);
            element.onended();
        });
    }

    _rms(analyser, buffer) {
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (const sample of buffer) sum += sample * sample;
        return Math.sqrt(sum / buffer.length);
    }

    _tick() {
        if (!this.context || !this.micAnalyser) return;
        const micBuffer = new Float32Array(this.micAnalyser.fftSize);
        const rms = this._rms(this.micAnalyser, micBuffer);
        $('mic-meter-fill').style.width = `${Math.min(100, rms * 900)}%`;
        const now = performance.now();

        if (this.playing || this.playQueue.length) {
            if (this.speechAnalyser) {
                const speechBuffer = new Float32Array(this.speechAnalyser.fftSize);
                const speechRms = this._rms(this.speechAnalyser, speechBuffer);
                this.stage.setSpeakingLevel(Math.min(1, speechRms * 9));
                if (this.currentAudio) {
                    this.stage.updateSpeechTiming(this.currentAudio.currentTime, this.currentAudio.duration);
                }
            }
            if (rms > this.BARGE_RMS) {
                this._stopPlayback();
                if (this.ws?.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'barge_in' }));
                }
                this.talking = true;
                this.talkStarted = now;
                this.lastVoice = now;
                this._startRecorder();
            }
        } else if (!this.talking) {
            if (rms > this.VAD_START_RMS) {
                this.talking = true;
                this.talkStarted = now;
                this.lastVoice = now;
                this._startRecorder();
            }
        } else if (rms > this.VAD_KEEP_RMS) {
            this.lastVoice = now;
        } else if (now - this.lastVoice > this.END_SILENCE_MS) {
            this.talking = false;
            if (now - this.talkStarted - this.END_SILENCE_MS > this.MIN_UTTER_MS) {
                this._shipUtterance();
            } else {
                try { this.recorder?.stop(); } catch (_) { /* recorder already stopped */ }
                this.recorder = null;
                this.recorderChunks = [];
            }
        }
        this.frame = requestAnimationFrame(() => this._tick());
    }

    end() {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'end' }));
        }
        this.cleanup(true);
    }

    cleanup(closeSocket) {
        if (this.frame) cancelAnimationFrame(this.frame);
        this.frame = null;
        this._stopPlayback();
        try { this.recorder?.stop(); } catch (_) { /* recorder already stopped */ }
        this.recorder = null;
        this.media?.getTracks().forEach((track) => track.stop());
        this.media = null;
        this.context?.close().catch(() => {});
        this.context = null;
        if (closeSocket && this.ws) this.ws.close();
        this.ws = null;
        this.stage.setPhase('idle');
        setConnection('idle', 'body online');
        $('mic-meter-fill').style.width = '0%';
        $('start-call').disabled = false;
        $('end-call').disabled = true;
        $('identity-select').disabled = false;
    }
}


function renderCapabilities(avatar) {
    const inspection = avatar.inspection || {};
    const capabilities = inspection.capabilities || {};
    const labels = [
        ['body_rig', 'Body skeleton'],
        ['animation_clips', 'Motion clips'],
        ['facial_rig', 'Facial rig'],
        ['jaw_control', 'Jaw / lip-sync'],
        ['eye_control', 'Eyes / gaze'],
        ['blink_control', 'Autonomous blink'],
        ['speech_visemes', 'Speech visemes'],
        ['expression_control', 'Expression controls'],
        ['realtime_performance_ready', 'Realtime ready'],
    ];
    $('capability-grid').innerHTML = labels.map(([key, label]) =>
        `<div class="capability ${capabilities[key] ? 'ready' : ''}">${label}</div>`
    ).join('');

    let note = 'This body is waiting for inspection.';
    if (capabilities.realtime_performance_ready) {
        note = 'Performance controls detected. This derivative is ready for live voice and expression testing.';
    } else if (capabilities.body_rig) {
        note = 'The body rig is sound. The next derivative needs reusable motion, facial morphs, blinking, gaze, and lip-sync controls; the Tripo master stays untouched.';
    } else {
        note = 'This model needs body-rig repair before it can enter the shared Pack pipeline.';
    }
    $('readiness-note').textContent = note;
    $('inspection-json').textContent = JSON.stringify(inspection, null, 2);
}


async function init() {
    const stage = new AvatarStage($('avatar-canvas'));
    const call = new NativeVoiceCall(stage);
    let avatars = [];

    try {
        const data = await getJson('/api/avatars');
        avatars = data.avatars || [];
        if (!avatars.length) throw new Error('No avatar manifests or rigged GLBs were found.');
        const select = $('identity-select');
        select.innerHTML = avatars.map((avatar) =>
            `<option value="${avatar.identity}">${avatar.display_name}</option>`
        ).join('');
        const requested = new URLSearchParams(location.search).get('identity')?.toLowerCase();
        if (requested && avatars.some((avatar) => avatar.identity === requested)) select.value = requested;

        const loadSelected = async () => {
            const avatar = avatars.find((item) => item.identity === select.value);
            if (!avatar) return;
            $('avatar-title').textContent = avatar.display_name;
            $('avatar-version').textContent = avatar.version;
            $('stage-name').textContent = avatar.display_name;
            renderCapabilities(avatar);
            await stage.load(`${avatar.model_url}?v=${encodeURIComponent(avatar.version)}`);
            setConnection('idle', 'body online');
        };
        select.addEventListener('change', () => loadSelected().catch((error) => {
            appendLog('system', `Body load failed: ${error.message}`);
            setConnection('error', 'body error');
        }));
        await loadSelected();
    } catch (error) {
        $('stage-loading').textContent = error.message;
        setConnection('error', 'body error');
        appendLog('system', error.message);
    }

    $('start-call').addEventListener('click', async () => {
        const avatar = avatars.find((item) => item.identity === $('identity-select').value);
        if (!avatar) return;
        try {
            setConnection('thinking', 'joining call');
            await call.start(avatar.display_name, $('voice-select').value);
        } catch (error) {
            appendLog('system', `Microphone or call start failed: ${error.message}`);
            setConnection('error', 'call start failed');
            call.cleanup(true);
        }
    });
    $('end-call').addEventListener('click', () => call.end());
    $('clear-log').addEventListener('click', () => {
        $('call-log').innerHTML = '<p class="log-system">Transcript cleared.</p>';
    });
}


init();
