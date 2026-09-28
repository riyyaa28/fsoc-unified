
import * as THREE from "../../node_modules/three/build/three.module.js";

// ============================================================
// FSOC 3D AIRSPACE
// ============================================================

// Scene
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x06152a);
scene.fog = new THREE.FogExp2(0x06152a, 0.00035);

// Camera
const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 5000);
const cameraTarget = new THREE.Vector3(0, 80, 0);
camera.position.set(700, 420, 780);
camera.lookAt(cameraTarget);

// Renderer
let renderer;

try {
    renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: false,
        powerPreference: "high-performance"
    });
} catch (error) {
    console.error("WebGL initialization failed:", error);
    throw error;
}

renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.35;

// Viewport
const sceneContainer = document.getElementById("scene");

if (!sceneContainer) {
    throw new Error('Missing HTML element with id="scene"');
}

// Keep #scene pinned to the full window (style.css: position absolute,
// inset 0). Forcing position: relative here collapsed it to a 400px strip.
sceneContainer.style.position = "absolute";
sceneContainer.style.inset = "0";
sceneContainer.style.overflow = "hidden";

const canvas = renderer.domElement;
canvas.style.position = "absolute";
canvas.style.inset = "0";
canvas.style.width = "100%";
canvas.style.height = "100%";
canvas.style.display = "block";
canvas.style.zIndex = "0";
canvas.style.touchAction = "none";

sceneContainer.appendChild(canvas);

const aircraftHud = document.getElementById("aircraft-hud");
const txMarker = document.getElementById("tx-marker");
const rxMarker = document.getElementById("rx-marker");

function resizeRenderer() {
    const bounds = sceneContainer.getBoundingClientRect();
    const width = Math.floor(bounds.width);
    const height = Math.floor(bounds.height);

    if (width <= 0 || height <= 0) return;

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height, false);

    camera.aspect = width / height;
    camera.updateProjectionMatrix();

    renderer.render(scene, camera);
}

const resizeObserver = new ResizeObserver(resizeRenderer);
resizeObserver.observe(sceneContainer);
window.addEventListener("resize", resizeRenderer);
window.addEventListener("pageshow", resizeRenderer);

requestAnimationFrame(() => {
    resizeRenderer();
    requestAnimationFrame(resizeRenderer);
});

// ============================================================
// CAMERA CONTROLS
// ============================================================

const orbit = new THREE.Spherical();
const orbitOffset = new THREE.Vector3();

const controls = {
    target: cameraTarget,
    minDistance: 180,
    maxDistance: 1200,
    maxPolarAngle: Math.PI * 0.48,

    getDistance() {
        return camera.position.distanceTo(this.target);
    },
    update() {
        orbitOffset.copy(camera.position).sub(this.target);
        orbit.setFromVector3(orbitOffset);

        orbit.radius = THREE.MathUtils.clamp(
            orbit.radius,
            this.minDistance,
            this.maxDistance
        );

        orbit.phi = THREE.MathUtils.clamp(
            orbit.phi,
            0.03,
            this.maxPolarAngle
        );

        orbit.makeSafe();
        camera.position.copy(
            this.target.clone().add(orbitOffset.setFromSpherical(orbit))
        );
        camera.lookAt(this.target);
    }
};
controls.target.set(0, 160, 0);
controls.update();
let pointerDown = false;
let draggingReceiver = false;
let lastPointerX = 0;
let lastPointerY = 0;
const dragRaycaster = new THREE.Raycaster();
const dragPointer = new THREE.Vector2();
const dragPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -80);
const dragPoint = new THREE.Vector3();

canvas.addEventListener("pointerdown", (event) => {
    const bounds = canvas.getBoundingClientRect();
    dragPointer.set(
        ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
        -((event.clientY - bounds.top) / bounds.height) * 2 + 1
    );
    dragRaycaster.setFromCamera(dragPointer, camera);
    if (dragRaycaster.intersectObject(rxDrone, true).length) {
        draggingReceiver = true;
        dragPlane.constant = -rxDrone.position.y;
        canvas.setPointerCapture(event.pointerId);
        return;
    }
    pointerDown = true;
    lastPointerX = event.clientX;
    lastPointerY = event.clientY;

    canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {
    if (draggingReceiver) {
        const bounds = canvas.getBoundingClientRect();
        dragPointer.set(
            ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
            -((event.clientY - bounds.top) / bounds.height) * 2 + 1
        );
        dragRaycaster.setFromCamera(dragPointer, camera);
        if (dragRaycaster.ray.intersectPlane(dragPlane, dragPoint)) {
            rxDrone.position.copy(dragPoint);
            enforceSeparation();
            simulationTime = 0;
            randomTargetValid = false;
            transitioning = false;
        }
        return;
    }
    if (!pointerDown) return;

    orbitOffset.copy(camera.position).sub(controls.target);
    orbit.setFromVector3(orbitOffset);

    orbit.theta -= (event.clientX - lastPointerX) * 0.005;
    orbit.phi = THREE.MathUtils.clamp(
        orbit.phi - (event.clientY - lastPointerY) * 0.005,
        0.03,
        controls.maxPolarAngle
    );

    orbit.makeSafe();

    camera.position.copy(
        controls.target.clone().add(orbitOffset.setFromSpherical(orbit))
    );

    lastPointerX = event.clientX;
    lastPointerY = event.clientY;
    controls.update();
});

canvas.addEventListener("pointerup", () => {
    pointerDown = false;
    draggingReceiver = false;
});

canvas.addEventListener("pointercancel", () => {
    pointerDown = false;
    draggingReceiver = false;
});

canvas.addEventListener("wheel", (event) => {
    event.preventDefault();

    orbitOffset.copy(camera.position).sub(controls.target);

    const distance = THREE.MathUtils.clamp(
        orbitOffset.length() * Math.exp(event.deltaY * 0.001),
        controls.minDistance,
        controls.maxDistance
    );

    orbitOffset.setLength(distance);
    camera.position.copy(controls.target).add(orbitOffset);
    controls.update();
}, { passive: false });

// ============================================================
// LIGHTING
// ============================================================

scene.add(new THREE.HemisphereLight(0x6ca8d8, 0x020611, 2));

const keyLight = new THREE.DirectionalLight(0xb8dcff, 3.5);
keyLight.position.set(-300, 500, 250);
scene.add(keyLight);

const rimLight = new THREE.DirectionalLight(0x00dfff, 3);
rimLight.position.set(400, 250, -500);
scene.add(rimLight);

const txLight = new THREE.PointLight(0x00eaff, 18, 600);
const rxLight = new THREE.PointLight(0x00eaff, 24, 650);
scene.add(txLight, rxLight);
const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(20000, 20000),
    new THREE.MeshStandardMaterial({
        color: 0x06121e,
        metalness: 0.45,
        roughness: 0.82
    })
);

ground.rotation.x = -Math.PI / 2;
ground.position.y = -65;
scene.add(ground);

// The floor grid is drawn procedurally on a plane instead of with
// GridHelper line segments. Long lines that pass under/behind the camera
// get dropped by line clipping on some WebGL backends (including the
// software renderer QtWebEngine can fall back to), which left most of the
// floor without grid. Triangles clip reliably, so a shaded plane covers
// the whole floor out to the far plane.
// Same look as before: cyan axis lines, 40-unit major lines (opacity
// 0.35) and a faint 10-unit fine grid (opacity 0.2).
const gridMaterial = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    fog: true,
    uniforms: THREE.UniformsUtils.merge([
        THREE.UniformsLib.fog,
        {
            axisColor: { value: new THREE.Color(0x00dfff) },
            majorColor: { value: new THREE.Color(0x174052) },
            minorColor: { value: new THREE.Color(0x041b2a) },
            majorOpacity: { value: 0.35 },
            minorOpacity: { value: 0.2 },
            majorSpacing: { value: 40 },
            minorSpacing: { value: 10 }
        }
    ]),
    vertexShader: /* glsl */ `
        #include <fog_pars_vertex>
        varying vec2 vWorldXZ;

        void main() {
            vec4 worldPosition = modelMatrix * vec4(position, 1.0);
            vWorldXZ = worldPosition.xz;
            vec4 mvPosition = viewMatrix * worldPosition;
            gl_Position = projectionMatrix * mvPosition;
            #include <fog_vertex>
        }
    `,
    fragmentShader: /* glsl */ `
        uniform vec3 axisColor;
        uniform vec3 majorColor;
        uniform vec3 minorColor;
        uniform float majorOpacity;
        uniform float minorOpacity;
        uniform float majorSpacing;
        uniform float minorSpacing;
        varying vec2 vWorldXZ;
        #include <fog_pars_fragment>

        // ~1px anti-aliased line coverage; fades out once the cells get
        // smaller than a couple of pixels so the horizon doesn't moire.
        float gridLine(vec2 coord, float spacing) {
            vec2 cell = coord / spacing;
            vec2 width = fwidth(cell);
            vec2 dist = abs(fract(cell - 0.5) - 0.5) / width;
            float line = 1.0 - min(min(dist.x, dist.y), 1.0);
            return line * (1.0 - smoothstep(0.25, 0.6, max(width.x, width.y)));
        }

        void main() {
            float minor = gridLine(vWorldXZ, minorSpacing) * minorOpacity;
            float major = gridLine(vWorldXZ, majorSpacing) * majorOpacity;

            vec2 axisDist = abs(vWorldXZ) / fwidth(vWorldXZ);
            float axis = (1.0 - min(min(axisDist.x, axisDist.y), 1.0)) * majorOpacity;

            vec3 color = minorColor;
            float alpha = minor;
            if (major >= alpha) {
                color = majorColor;
                alpha = major;
            }
            if (axis >= alpha && axis > 0.0) {
                color = axisColor;
                alpha = axis;
            }
            if (alpha <= 0.001) discard;

            gl_FragColor = vec4(color, alpha);
            #include <tonemapping_fragment>
            #include <colorspace_fragment>
            #include <fog_fragment>
        }
    `
});

// Same footprint as the ground plane; everything past the camera's far
// plane (5000) is clipped anyway, and fog hides the edge.
const grid = new THREE.Mesh(new THREE.PlaneGeometry(20000, 20000), gridMaterial);
grid.rotation.x = -Math.PI / 2;
grid.position.set(0, -62, 0);
// Draw before other transparent objects so it never blends over them.
grid.renderOrder = -1;
scene.add(grid);

// ============================================================
// STARS
// ============================================================

const starPositions = new Float32Array(1100 * 3);

for (let i = 0; i < 1100; i++) {
    starPositions[i * 3] = (Math.random() - 0.5) * 2600;
    starPositions[i * 3 + 1] = Math.random() * 1200 + 120;
    starPositions[i * 3 + 2] = (Math.random() - 0.5) * 2600;
}

const starGeometry = new THREE.BufferGeometry();
starGeometry.setAttribute(
    "position",
    new THREE.BufferAttribute(starPositions, 3)
);

const stars = new THREE.Points(
    starGeometry,
    new THREE.PointsMaterial({
        color: 0x8bdfff,
        size: 1.7,
        transparent: true,
        opacity: 0.7
    })
);
scene.add(stars);

// ============================================================
// DRONE MODEL
// ============================================================

function createDrone() {
    const drone = new THREE.Group();

    const bodyMaterial = new THREE.MeshBasicMaterial({
        color: 0x43a8d4,
        fog: false
    });

    const darkMaterial = new THREE.MeshBasicMaterial({
        color: 0x1e6388,
        fog: false
    });

    const rotorMaterial = new THREE.MeshBasicMaterial({
        color: 0x00ddff,
        fog: false
    });

    const cyanMaterial = new THREE.MeshBasicMaterial({
        color: 0x8bffff,
        fog: false
    });

    const body = new THREE.Mesh(
        new THREE.BoxGeometry(36, 12, 28),
        bodyMaterial
    );
    drone.add(body);

    const topShell = new THREE.Mesh(
        new THREE.BoxGeometry(24, 6, 19),
        darkMaterial
    );
    topShell.position.y = 8;
    drone.add(topShell);

    drone.add(
        new THREE.Mesh(new THREE.BoxGeometry(112, 5, 7), darkMaterial),
        new THREE.Mesh(new THREE.BoxGeometry(7, 5, 112), darkMaterial)
    );

    for (const x of [-48, 48]) {
        for (const z of [-48, 48]) {
            const rotor = new THREE.Mesh(
                new THREE.CylinderGeometry(15, 15, 3, 24),
                rotorMaterial
            );
            rotor.position.set(x, 5, z);
            drone.add(rotor);

            const hub = new THREE.Mesh(
                new THREE.CylinderGeometry(5, 5, 5, 16),
                bodyMaterial
            );
            hub.position.set(x, 7, z);
            drone.add(hub);

            const light = new THREE.Mesh(
                new THREE.SphereGeometry(3.5, 12, 12),
                cyanMaterial
            );
            light.position.set(x, 10, z);
            drone.add(light);
        }
    }

    const frontGlow = new THREE.Mesh(
        new THREE.SphereGeometry(8, 20, 20),
        cyanMaterial
    );
    frontGlow.position.set(19, 1, 0);
    drone.add(frontGlow);

    return drone;
}

const txDrone = createDrone();
txDrone.scale.setScalar(1.6);
txDrone.position.set(-330, 120, 0);
scene.add(txDrone);

const rxDrone = createDrone();
rxDrone.scale.setScalar(1.4);
rxDrone.position.set(0, 80, 0);
scene.add(rxDrone);
const MIN_DRONE_SEPARATION = 5 / 0.3;
function enforceSeparation() {
    const offset = rxDrone.position.clone().sub(txDrone.position);
    if (offset.length() < MIN_DRONE_SEPARATION) {
        if (offset.lengthSq() < 1e-6) offset.set(0, 0, 1);
        rxDrone.position.copy(txDrone.position).add(offset.normalize().multiplyScalar(MIN_DRONE_SEPARATION));
    }
}

// Receiver beacon
const beacon = new THREE.Group();

const beaconCore = new THREE.Mesh(
    new THREE.SphereGeometry(7, 20, 20),
    new THREE.MeshBasicMaterial({ color: 0xffffff })
);

const beaconHalo = new THREE.Mesh(
    new THREE.SphereGeometry(13, 16, 16),
    new THREE.MeshBasicMaterial({
        color: 0x35f4ff,
        transparent: true,
        opacity: 0.28,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    })
);

beacon.add(beaconHalo, beaconCore);
beacon.position.set(0, 22, 0);
rxDrone.add(beacon);
let beaconHidden = false;

// Decoy drones use the same model at half the receiver's scale.
const decoyDrones = [];
const decoyMovementBounds = 520;

for (let index = 0; index < 10; index++) {
    const target = createDrone();
    target.scale.setScalar(rxDrone.scale.x * 0.5);
    const angle = index * 2.399963229728653;
    const radius = 190 + (index % 4) * 72;
    target.position.set(
        Math.cos(angle) * radius,
        55 + (index % 3) * 24,
        Math.sin(angle) * radius
    );
    target.visible = index < 4;
    target.userData.phase = index * 0.73;
    target.userData.speed = 45 + (index % 4) * 12;
    target.userData.destination = new THREE.Vector3();
    target.userData.destinationValid = false;
    scene.add(target);
    decoyDrones.push(target);
}

function chooseDecoyDestination(drone) {
    drone.userData.destination.set(
        (Math.random() - 0.5) * decoyMovementBounds * 2,
        45 + Math.random() * 170,
        (Math.random() - 0.5) * decoyMovementBounds * 2
    );
    drone.userData.destinationValid = true;
}

function updateDecoys(deltaTime, elapsed) {
    decoyDrones.forEach((drone) => {
        if (!drone.userData.destinationValid) chooseDecoyDestination(drone);
        const destination = drone.userData.destination;
        const dx = destination.x - drone.position.x;
        const dy = destination.y - drone.position.y;
        const dz = destination.z - drone.position.z;
        const distance = Math.hypot(dx, dy, dz);
        if (distance < 8) {
            drone.userData.destinationValid = false;
            return;
        }

        const step = Math.min(drone.userData.speed * deltaTime, distance);
        drone.position.x += dx / distance * step;
        drone.position.y += dy / distance * step;
        drone.position.z += dz / distance * step;
        drone.rotation.y = Math.atan2(dx, dz);
        drone.rotation.z = Math.sin(elapsed * 2 + drone.userData.phase) * 0.04;
    });
}

// ============================================================
// RECEIVER RINGS
// ============================================================

function createRing(inner, outer, opacity) {
    const ring = new THREE.Mesh(
        new THREE.RingGeometry(inner, outer, 64),
        new THREE.MeshBasicMaterial({
            color: 0x00eaff,
            transparent: true,
            opacity,
            blending: THREE.AdditiveBlending,
            side: THREE.DoubleSide,
            depthWrite: false
        })
    );

    ring.rotation.x = -Math.PI / 2;
    ring.position.y = -63;
    rxDrone.add(ring);
    return ring;
}

const ring1 = createRing(35, 38, 0.7);
const ring2 = createRing(58, 61, 0.55);
const ring3 = createRing(82, 84, 0.4);

const verticalRing = new THREE.Mesh(
    new THREE.RingGeometry(34, 37, 64),
    new THREE.MeshBasicMaterial({
        color: 0x5cf6ff,
        transparent: true,
        opacity: 0.4,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        depthWrite: false
    })
);
verticalRing.rotation.y = Math.PI / 2;
rxDrone.add(verticalRing);

// ============================================================
// COMMUNICATION BEAM
// ============================================================

const beamCore = new THREE.Mesh(
    new THREE.CylinderGeometry(3.5, 2, 1, 16, 1, true),
    new THREE.MeshBasicMaterial({
        color: 0x55f4ff,
        transparent: true,
        opacity: 0.72,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    })
);

const beamGlow = new THREE.Mesh(
    new THREE.CylinderGeometry(13, 6, 1, 24, 1, true),
    new THREE.MeshBasicMaterial({
        color: 0x00bfff,
        transparent: true,
        opacity: 0.13,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    })
);

const beamPulse = new THREE.Mesh(
    new THREE.SphereGeometry(7, 16, 16),
    new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    })
);

scene.add(beamCore, beamGlow, beamPulse);

// ============================================================
// ENVIRONMENT CONTROLS
// ============================================================

const disturbanceLevels = {
    fog: 0,
    rain: 0,
    gaussian: 0,
    "salt-pepper": 0
};

function setDisturbance(effect, level) {
    if (!(effect in disturbanceLevels)) return;

    const control = document.querySelector(
        `.environment-item[data-effect="${effect}"]`
    );

    const bars = control
        ? [...control.querySelectorAll(".level-indicator span")]
        : [];

    const maxLevel = bars.length || 4;
    const safeLevel = THREE.MathUtils.clamp(
        Math.round(Number(level) || 0), 0, maxLevel
    );

    disturbanceLevels[effect] = safeLevel;

    bars.forEach((bar, index) => {
        bar.classList.toggle("active", index < safeLevel);
    });

    if (control) {
        control.setAttribute(
            "aria-label", `${effect} disturbance level ${safeLevel}`
        );
        control.setAttribute("aria-pressed", String(safeLevel > 0));
    }
}

document.querySelectorAll(".environment-item[data-effect]").forEach((control) => {
    const effect = control.dataset.effect;
    const bars = [...control.querySelectorAll(".level-indicator span")];

    if (!(effect in disturbanceLevels)) return;

    const cycle = () => {
        setDisturbance(
            effect,
            (disturbanceLevels[effect] + 1) % (bars.length + 1)
        );
    };

    control.addEventListener("click", cycle);
    control.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            cycle();
        }
    });

    setDisturbance(effect, 0);
});

window.fsocSetDisturbanceLevel = setDisturbance;

// ============================================================
// SIMULATION
// ============================================================

let running = true;
let pattern = "circular";
let autoMode = false;
let autoSwitchRemaining = 9;
let simulationTime = 0;
let transitioning = false;
let randomTarget = new THREE.Vector3();
let randomTargetValid = false;

const trailPoints = [];
const maxTrailPoints = 700;

const trailPositions = new Float32Array(maxTrailPoints * 3);
const trailPositionAttribute = new THREE.BufferAttribute(
    trailPositions,
    3
);

trailPositionAttribute.setUsage(THREE.DynamicDrawUsage);

const trailGeometry = new THREE.BufferGeometry();
trailGeometry.setAttribute("position", trailPositionAttribute);
trailGeometry.setDrawRange(0, 0);

const trail = new THREE.Line(
    trailGeometry,
    new THREE.LineBasicMaterial({
        color: 0x00dfff,
        transparent: true,
        opacity: 0.6,
        blending: THREE.AdditiveBlending
    })
);

trail.visible = false;
scene.add(trail);

function clearTrail() {
    trailPoints.length = 0;
    trailGeometry.setDrawRange(0, 0);
    trail.visible = false;
}

function updateTrail() {
    const point = rxDrone.position.clone();

    if (
        trailPoints.length === 0 ||
        trailPoints[trailPoints.length - 1].distanceTo(point) > 2
    ) {
        trailPoints.push(point);
    }

    if (trailPoints.length > maxTrailPoints) {
        trailPoints.shift();
    }

    for (let index = 0; index < trailPoints.length; index++) {
        trailPositions[index * 3] = trailPoints[index].x;
        trailPositions[index * 3 + 1] = trailPoints[index].y;
        trailPositions[index * 3 + 2] = trailPoints[index].z;
    }

    trailPositionAttribute.needsUpdate = true;
    trailGeometry.setDrawRange(0, trailPoints.length);
    trail.visible = trailPoints.length >= 2;
}

function chooseRandomTarget() {
    randomTarget.set(
        (Math.random() - 0.5) * 680,
        rxDrone.position.y,
        (Math.random() - 0.5) * 680
    );
    randomTargetValid = true;
}

function updateRandom(deltaTime) {
    if (!randomTargetValid) chooseRandomTarget();

    const dx = randomTarget.x - rxDrone.position.x;
    const dz = randomTarget.z - rxDrone.position.z;
    const distance = Math.hypot(dx, dz);

    if (distance < 10) {
        randomTargetValid = false;
        return;
    }

    const step = Math.min(110 * deltaTime, distance);
    rxDrone.position.x += (dx / distance) * step;
    rxDrone.position.z += (dz / distance) * step;
}

function receiverAltitude(time) {
    // Keep RX near the operating altitude so the camera and ground grid stay stable.
    return 105 + 22 * Math.sin(time * 0.42);
}

function chooseAutoPattern() {
    const choices = ["random", "circular", "figure8", "straight"];
    const alternatives = choices.filter((choice) => choice !== pattern);
    return alternatives[Math.floor(Math.random() * alternatives.length)];
}

function updatePattern(deltaTime) {
    const dropdownValue = patternSelect?.value?.toLowerCase();
    const activeSelection = autoMode ? "auto" : pattern;
    if (dropdownValue && dropdownValue !== activeSelection) {
        setPattern(dropdownValue);
    }

    if (autoMode) {
        autoSwitchRemaining -= deltaTime;

        if (autoSwitchRemaining <= 0) {
            pattern = chooseAutoPattern();
            autoSwitchRemaining = 8 + Math.random() * 5;
            simulationTime = 0;
            randomTargetValid = false;
            transitioning = true;
            return;
        }
    }

    simulationTime += deltaTime;
    rxDrone.position.y = receiverAltitude(simulationTime);

    if (pattern === "random") {
        updateRandom(deltaTime);
    } else if (pattern === "circular") {
        const angle = simulationTime * 0.55;
        const radius = 255;
        rxDrone.position.x = radius * Math.sin(angle);
        rxDrone.position.z = radius * (1 - Math.cos(angle));
    } else if (pattern === "figure8") {
        const angle = simulationTime * 0.5;

        rxDrone.position.x =
            305 * Math.sin(angle) + 65 * Math.sin(angle * 3);

        rxDrone.position.z =
            175 * Math.sin(angle * 2) + 45 * Math.sin(angle * 3);
    } else if (pattern === "straight") {
        const t = simulationTime * 0.32;
        rxDrone.position.x = 390 * Math.sin(t);
        rxDrone.position.z = 105 * Math.sin(t * 2.4);
    }
    enforceSeparation();
}

function transitionToOrigin(deltaTime) {
    const x = rxDrone.position.x;
    const z = rxDrone.position.z;
    const distance = Math.hypot(x, z);

    if (distance < 2) {
        rxDrone.position.x = 0;
        rxDrone.position.z = 0;
        transitioning = false;
        simulationTime = 0;
        randomTargetValid = false;
        clearTrail();
        return;
    }

    const step = Math.min(160 * deltaTime, distance);
    rxDrone.position.x += (-x / distance) * step;
    rxDrone.position.z += (-z / distance) * step;
}

// ============================================================
// BEAM UPDATE
// ============================================================

const beamAxis = new THREE.Vector3(0, 1, 0);
const beamQuaternion = new THREE.Quaternion();

function updateBeam(elapsed) {
    const start = txDrone.position.clone();
    const end = rxDrone.position.clone();
    const direction = end.clone().sub(start);
    const distance = direction.length();

    if (distance < 0.001) {
        beamCore.visible = false;
        beamGlow.visible = false;
        beamPulse.visible = false;
        return;
    }

    beamCore.visible = true;
    beamGlow.visible = true;
    beamPulse.visible = true;

    const gaussian = disturbanceLevels.gaussian / 4;
    const wobbleX = Math.sin(elapsed * 13) * gaussian * 5;
    const wobbleZ = Math.sin(elapsed * 9.7 + 1.4) * gaussian * 5;

    const midpoint = start.clone().add(end).multiplyScalar(0.5);
    midpoint.x += wobbleX;
    midpoint.z += wobbleZ;

    beamCore.position.copy(midpoint);
    beamGlow.position.copy(midpoint);

    beamCore.scale.set(1, distance, 1);
    beamGlow.scale.set(1, distance, 1);

    beamQuaternion.setFromUnitVectors(
        beamAxis,
        direction.normalize()
    );

    beamCore.quaternion.copy(beamQuaternion);
    beamGlow.quaternion.copy(beamQuaternion);

    const fogAttenuation = 1 - disturbanceLevels.fog * 0.13;
    const rainAttenuation = 1 - disturbanceLevels.rain * 0.1;

    const speckle =
        disturbanceLevels["salt-pepper"] > 0 &&
        Math.sin(elapsed * 41) >
            1 - disturbanceLevels["salt-pepper"] * 0.16
            ? 0.18
            : 1;

    beamCore.material.opacity =
        0.72 * fogAttenuation * rainAttenuation * speckle;

    beamGlow.material.opacity =
        0.13 * fogAttenuation * rainAttenuation * speckle;

    const pulse = (elapsed * 0.7) % 1;
    beamPulse.position.copy(
        start.clone().lerp(end, pulse).add(
            new THREE.Vector3(
                wobbleX * Math.sin(pulse * Math.PI),
                0,
                wobbleZ * Math.sin(pulse * Math.PI)
            )
        )
    );
}

// ============================================================
// UI ELEMENTS
// ============================================================

function findElement(...ids) {
    for (const id of ids) {
        const element = document.getElementById(id);
        if (element) return element;
    }
    return null;
}

const startButton = findElement(
    "start-button", "startBtn", "startButton", "start", "START"
);
const pauseButton = findElement(
    "pause-button", "pauseBtn", "pauseButton", "pause", "PAUSE"
);
const resetButton = findElement(
    "reset-button", "resetBtn", "resetButton", "reset", "RESET"
);
const patternSelect = findElement(
    "pattern-select", "patternSelect", "pattern",
    "movementPattern", "movement"
);
const beaconToggle = document.getElementById("beacon-toggle");
const decoyCountSlider = document.getElementById("decoy-count");
const decoyCountValue = document.getElementById("decoy-count-value");

const trackingStatus = findElement("tracking-status");
const detectionSource = findElement("detection-source");
const confidenceDisplay = findElement("confidence");
const errorDisplay = findElement("tracking-error");
const rxState = findElement("rx-state");
const panDisplay = findElement("pan-value");
const tiltDisplay = findElement("tilt-value");
const zoomDisplay = findElement("zoom-value");
const terminalBeacon = findElement("terminal-beacon");
const acquisitionDisplay = document.getElementById("acquisition-time");
let trackingElapsed = 0;
let acquisitionTime = null;
let reacquisitionTime = null;
let reacquisitionStarted = null;

function setBeaconHidden(hidden) {
    beaconHidden = Boolean(hidden);
    [beacon, ring1, ring2, ring3, verticalRing].forEach((object) => {
        object.visible = !beaconHidden;
    });
    if (beaconToggle) {
        beaconToggle.textContent = beaconHidden ? "SHOW BEACON" : "HIDE BEACON";
        beaconToggle.setAttribute("aria-pressed", String(beaconHidden));
    }
    if (terminalBeacon) {
        terminalBeacon.classList.toggle("beacon-offscreen", beaconHidden);
    }
}

// ============================================================
// AIRCRAFT HUD
// ============================================================

function updateAircraftMarker(drone, marker) {
    if (!marker || !aircraftHud) return;

    drone.updateMatrixWorld(true);
    camera.updateMatrixWorld(true);

    const centerWorld = drone.getWorldPosition(new THREE.Vector3());
    const center = centerWorld.clone().project(camera);
    const nose = drone.localToWorld(
        new THREE.Vector3(48, 0, 0)
    ).project(camera);

    const width = aircraftHud.clientWidth;
    const height = aircraftHud.clientHeight;

    if (!width || !height || center.z < -1 || center.z > 1) {
        marker.style.display = "none";
        return;
    }

    marker.style.display = "";

    const screenX = (center.x * 0.5 + 0.5) * width;
    const screenY = (-center.y * 0.5 + 0.5) * height;

    const clampedX = THREE.MathUtils.clamp(screenX, 90, width - 90);
    const clampedY = THREE.MathUtils.clamp(screenY, 105, height - 85);

    const noseX = (nose.x * 0.5 + 0.5) * width;
    const noseY = (-nose.y * 0.5 + 0.5) * height;
    const angle = Math.atan2(noseY - screenY, noseX - screenX);

    const distance = camera.position.distanceTo(centerWorld);
    const pixelsPerUnit =
        height / (
            2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) *
            distance
        );

    const scale = THREE.MathUtils.clamp(
        150 * drone.scale.x * pixelsPerUnit / 176,
        0.95,
        2.0
    );

    marker.style.left = `${clampedX}px`;
    marker.style.top = `${clampedY}px`;
    marker.style.transform =
        `translate(-50%, -50%) rotate(${angle}rad) scale(${scale})`;

    marker.classList.toggle(
        "aircraft-edge",
        clampedX !== screenX || clampedY !== screenY
    );
}

function drawAircraftOverlay() {
    updateAircraftMarker(txDrone, txMarker);
    updateAircraftMarker(rxDrone, rxMarker);
}

// ============================================================
// TRACKING AND TELEMETRY
// ============================================================

const kalmanPosition = beacon.getWorldPosition(new THREE.Vector3());
const kalmanVelocity = new THREE.Vector3();
let trackingLostTime = 0;

function updateBeaconTracking(deltaTime, elapsed) {
    const dt = Math.max(deltaTime, 1 / 120);
    trackingElapsed += dt;
    const actualPosition = beacon.getWorldPosition(new THREE.Vector3());
    const projected = actualPosition.clone().project(camera);

    const usingPython2D =
        document.getElementById("app")?.classList.contains("view-2d");

    const inView =
        projected.x >= -1 && projected.x <= 1 &&
        projected.y >= -1 && projected.y <= 1 &&
        projected.z >= -1 && projected.z <= 1;

    if (terminalBeacon && !usingPython2D) {
        terminalBeacon.style.setProperty(
            "--beacon-x",
            `${THREE.MathUtils.clamp(projected.x * 70, -70, 70)}px`
        );
        terminalBeacon.style.setProperty(
            "--beacon-y",
            `${THREE.MathUtils.clamp(-projected.y * 70, -70, 70)}px`
        );
        terminalBeacon.classList.toggle("beacon-offscreen", beaconHidden || !inView);
    }

    const severity = Object.values(disturbanceLevels)
        .reduce((sum, level) => sum + level, 0);

    const measurementChance = Math.max(0.18, 0.99 - severity * 0.05);
    const detected = !beaconHidden && inView &&
        Math.random() < Math.max(measurementChance, 0.98);

    const predictedPosition = kalmanPosition.clone()
        .addScaledVector(kalmanVelocity, dt);

    let error = actualPosition.distanceTo(kalmanPosition);

    if (detected) {
        if (acquisitionTime === null) acquisitionTime = trackingElapsed;
        if (reacquisitionStarted !== null) {
            reacquisitionTime = trackingElapsed - reacquisitionStarted;
            reacquisitionStarted = null;
        }
        const innovation = actualPosition.clone().sub(predictedPosition);
        const gain = Math.max(0.92, 0.98 - severity * 0.004);

        kalmanPosition.copy(predictedPosition)
            .addScaledVector(innovation, gain);

        kalmanVelocity.addScaledVector(
            innovation,
            Math.min(4, 0.08 / dt)
        );

        trackingLostTime = 0;
        error = actualPosition.distanceTo(kalmanPosition);
    } else {
        if (trackingLostTime <= 0 && acquisitionTime !== null) reacquisitionStarted = trackingElapsed;
        kalmanPosition.copy(predictedPosition);
        kalmanVelocity.multiplyScalar(0.985);
        trackingLostTime += dt;
    }

    if (acquisitionDisplay) {
        const acquisition = acquisitionTime === null ? "--" : acquisitionTime.toFixed(2);
        const reacquisition = reacquisitionTime === null ? "--" : reacquisitionTime.toFixed(2);
        acquisitionDisplay.textContent = `${acquisition} / ${reacquisition} s`;
    }

    // Leave the camera anchored to the airspace. Following RX here keeps the
    // receiver centered and makes its selected movement pattern look static.

    const recentlyTracked = trackingLostTime < 0.25;

    const source = detected || recentlyTracked
        ? severity < 4 ? "YOLO"
            : severity < 9 ? "YOLO + KALMAN"
            : "KALMAN FUSION"
        : trackingLostTime > 2.8
            ? "KALMAN SEARCH"
            : "KALMAN PREDICTION";

    const status = detected || recentlyTracked
        ? severity >= 12 ? "DEGRADED" : "LOCKED"
        : trackingLostTime > 2.8 ? "SEARCHING" : "PREDICTING";

    const confidence = detected || recentlyTracked
        ? Math.max(48, 98 - severity * 2.5)
        : Math.max(0, 68 - trackingLostTime * 20 - severity);

    if (!usingPython2D) {
        if (trackingStatus) {
            trackingStatus.textContent = status;
            trackingStatus.classList.toggle(
                "status-lock", status === "LOCKED"
            );
            trackingStatus.classList.toggle(
                "status-warning",
                status === "DEGRADED" || status === "PREDICTING"
            );
            trackingStatus.classList.toggle(
                "status-lost", status === "SEARCHING"
            );
        }

        if (detectionSource) detectionSource.textContent = source;
        if (confidenceDisplay) {
            confidenceDisplay.textContent = `${confidence.toFixed(1)}%`;
        }
        if (errorDisplay) {
            errorDisplay.textContent = `${(error * 0.3).toFixed(1)} px`;
        }
        if (rxState) {
            rxState.textContent = beaconHidden
                ? "BEACON HIDDEN / PATH LIVE"
                : detected || recentlyTracked
                    ? "TRACKING"
                    : trackingLostTime > 2.8
                        ? "SEARCHING"
                        : "PREDICTING";
        }

        const dx = kalmanPosition.x - txDrone.position.x;
        const dy = kalmanPosition.y - txDrone.position.y;
        const dz = kalmanPosition.z - txDrone.position.z;

        if (panDisplay) {
            panDisplay.textContent =
                `${THREE.MathUtils.radToDeg(Math.atan2(dx, dz)).toFixed(1)}°`;
        }

        if (tiltDisplay) {
            tiltDisplay.textContent =
                `${THREE.MathUtils.radToDeg(
                    Math.atan2(dy, Math.hypot(dx, dz))
                ).toFixed(1)}°`;
        }

        if (zoomDisplay) {
            zoomDisplay.textContent =
                `${(880 / controls.getDistance()).toFixed(1)}×`;
        }
    }

    beacon.scale.setScalar(0.85 + Math.sin(elapsed * 5) * 0.12);
}

// ============================================================
// BUTTONS AND PATTERN SELECTION
// ============================================================

if (startButton) {
    startButton.addEventListener("click", () => {
        running = true;
    });
}

if (pauseButton) {
    pauseButton.addEventListener("click", () => {
        running = false;
    });
}

function resetSimulation() {
    running = false;
    setBeaconHidden(false);
    rxDrone.position.set(0, 80, 0);
    kalmanPosition.copy(beacon.getWorldPosition(new THREE.Vector3()));
    kalmanVelocity.set(0, 0, 0);
    trackingLostTime = 0;
    trackingElapsed = 0;
    acquisitionTime = null;
    reacquisitionTime = null;
    reacquisitionStarted = null;
    rxDrone.rotation.y = 0;
    simulationTime = 0;
    transitioning = false;
    randomTargetValid = false;
    clearTrail();
    updateBeam(0);
}

if (resetButton) {
    resetButton.addEventListener("click", resetSimulation);
}

function setPattern(value) {
    const requested = String(value).toLowerCase();
    const supportedPatterns = ["auto", "circular", "figure8", "straight", "random"];
    const selected = supportedPatterns.includes(requested) ? requested : "circular";

    autoMode = selected === "auto";
    pattern = autoMode ? chooseAutoPattern() : selected;
    autoSwitchRemaining = 8 + Math.random() * 5;
    transitioning = false;
    simulationTime = 0;
    randomTargetValid = false;
    if (pattern !== "random") {
        rxDrone.position.x = 0;
        rxDrone.position.z = 0;
    }

    if (patternSelect && patternSelect.value !== selected) {
        patternSelect.value = selected;
    }
    clearTrail();
}

if (patternSelect) {
    patternSelect.addEventListener("change", () => {
        setPattern(patternSelect.value);
    });
}

function setDecoyCount(value) {
    const count = THREE.MathUtils.clamp(Math.round(Number(value) || 0), 0, 10);
    decoyDrones.forEach((target, index) => {
        target.visible = index < count;
    });
    if (decoyCountSlider) decoyCountSlider.value = String(count);
    if (decoyCountValue) decoyCountValue.textContent = String(count);
}

if (decoyCountSlider) {
    decoyCountSlider.addEventListener("input", () => {
        setDecoyCount(decoyCountSlider.value);
    });
    setDecoyCount(decoyCountSlider.value);
}

if (beaconToggle) {
    beaconToggle.addEventListener("click", () => {
        setBeaconHidden(!beaconHidden);
        if (rxState) {
            rxState.textContent = beaconHidden ? "BEACON HIDDEN / PATH LIVE" : "TRACKING";
        }
    });
}

// ============================================================
// PYTHON / DASHBOARD API
// ============================================================

window.fsoc = {
    setReceiverPosition(x, y, z = 0) {
        rxDrone.position.set(x, y, z);
        enforceSeparation();
        updateBeam(performance.now() / 1000);
    },

    setTransmitterPosition(x, y, z = 0) {
        txDrone.position.set(x, y, z);
        enforceSeparation();
        updateBeam(performance.now() / 1000);
    },

    clearTrail() {
        clearTrail();
    },

    getReceiverPosition() {
        return {
            x: rxDrone.position.x,
            y: rxDrone.position.y,
            z: rxDrone.position.z
        };
    },

    getTransmitterPosition() {
        return {
            x: txDrone.position.x,
            y: txDrone.position.y,
            z: txDrone.position.z
        };
    },

    setRunning(value) {
        running = Boolean(value);
    },

    setPattern(value) {
        setPattern(value);
    },

    setDecoyCount(value) {
        setDecoyCount(value);
    },

    setBeaconHidden(value) {
        setBeaconHidden(value);
    },

    reset() {
        resetSimulation();
    }
};

// ============================================================
// ANIMATION LOOP
// ============================================================

let previousTime = performance.now() / 1000;
let elapsedTime = 0;
const cameraFollowDelta = new THREE.Vector3();

function animate(now) {
    requestAnimationFrame(animate);

    const currentTime = now / 1000;
    const deltaTime = Math.min(
        Math.max(currentTime - previousTime, 0),
        0.05
    );

    previousTime = currentTime;
    elapsedTime += deltaTime;

    if (running) {
        if (transitioning) {
            transitionToOrigin(deltaTime);
        } else {
            updatePattern(deltaTime);
            updateTrail();
        }
    }

    const pulse = 1 + Math.sin(elapsedTime * 3) * 0.12;
    ring1.scale.setScalar(pulse);
    ring2.scale.setScalar(1 + Math.sin(elapsedTime * 2) * 0.08);
    ring3.scale.setScalar(1 + Math.sin(elapsedTime * 1.5) * 0.06);

    updateDecoys(deltaTime, elapsedTime);
    decoyDrones.forEach((target) => {
        const blink = 0.92 + Math.sin(elapsedTime * 2.8 + target.userData.phase) * 0.08;
        target.scale.setScalar(rxDrone.scale.x * 0.5 * blink);
    });

    stars.rotation.y += deltaTime * 0.003;

    txLight.position.copy(txDrone.position);
    rxLight.position.copy(rxDrone.position);

    updateBeaconTracking(deltaTime, elapsedTime);
    updateBeam(elapsedTime);
    // Move the camera and orbit center together so manual rotation/zoom stays intact.
    cameraFollowDelta.copy(rxDrone.position).sub(controls.target);
    controls.target.add(cameraFollowDelta);
    camera.position.add(cameraFollowDelta);
    controls.update();

    renderer.render(scene, camera);
    drawAircraftOverlay();
}

// Initial camera and render
controls.update();
resizeRenderer();
updateBeam(0);
renderer.render(scene, camera);

requestAnimationFrame(animate);

window.fsocReady = true;

const engineStatus = document.getElementById("engine-status");
if (engineStatus) {
    engineStatus.textContent = "3D AIRSPACE / ONLINE";
}

window.fsocRefreshViewport = () => {
    resizeRenderer();
    controls.update();
    renderer.render(scene, camera);
    drawAircraftOverlay();
};

console.log("FSOC 3D Airspace initialized successfully.");
