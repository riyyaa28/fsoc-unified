
import * as THREE from "../../node_modules/three/build/three.module.js";

// ---- FSOC 3D AIRSPACE ----

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x06152a);
scene.fog = new THREE.FogExp2(0x06152a, 0.00035);

const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 5000);
const cameraTarget = new THREE.Vector3(0, 80, 0);
camera.position.set(700, 420, 780);
camera.lookAt(cameraTarget);

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

const sceneContainer = document.getElementById("scene");

if (!sceneContainer) {
    throw new Error('Missing HTML element with id="scene"');
}

sceneContainer.style.position = "absolute";
sceneContainer.style.inset = "0";
sceneContainer.style.width = "100%";
sceneContainer.style.height = "100%";
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
    const width = Math.floor(bounds.width || window.innerWidth);
    const height = Math.floor(bounds.height || window.innerHeight);

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

// ---- CAMERA CONTROLS ----

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
            patternTransition = null;
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

// ---- LIGHTING ----

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

// Extend the grid beyond the floor so no floor edge cuts through the view.
const grid = new THREE.GridHelper(
    200000,
    5000, // 40-unit line spacing
    0x00dfff,
    0x174052
);

grid.position.set(0, -62, 0);
grid.material.transparent = true;
grid.material.opacity = 0.35;
scene.add(grid);

const secondaryGrid = new THREE.GridHelper(
    200000, 20000, 0x062638, 0x041b2a
);
secondaryGrid.position.set(0, -61, 0);
secondaryGrid.material.transparent = true;
secondaryGrid.material.opacity = 0.2;
scene.add(secondaryGrid);

// ---- STARS ----

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

// ---- DRONE MODEL ----

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
const METERS_PER_WORLD_UNIT = 0.3;
const MIN_DRONE_SEPARATION = 5 / METERS_PER_WORLD_UNIT;
function enforceSeparation() {
    const offset = rxDrone.position.clone().sub(txDrone.position);
    if (offset.lengthSq() < MIN_DRONE_SEPARATION ** 2) {
        if (offset.lengthSq() < 1e-6) offset.set(0, 0, 1);
        rxDrone.position.copy(txDrone.position).add(offset.normalize().multiplyScalar(MIN_DRONE_SEPARATION));
    }
}

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

// ---- RECEIVER RINGS ----

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

// ---- COMMUNICATION BEAM ----

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

// ---- ENVIRONMENT CONTROLS ----

const disturbanceLevels = {
    fog: 0,
    rain: 0,
    gaussian: 0,
    "salt-pepper": 0,
    poisson: 0
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

    const changed = disturbanceLevels[effect] !== safeLevel;
    disturbanceLevels[effect] = safeLevel;
    if (changed) recordActivity("PARAMETER", `${effect} disturbance set to level ${safeLevel}`);

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

// ---- SIMULATION ----

let running = true;
let pattern = "circular";
let autoMode = false;
let autoSwitchRemaining = 9;
let simulationTime = 0;
let randomTarget = new THREE.Vector3();
let randomTargetValid = false;
let patternTransition = null;
const receiverVelocity = new THREE.Vector3();
const previousReceiverPosition = rxDrone.position.clone();
const PATTERN_TRANSITION_DURATION = 2.2;
const MIN_RECEIVER_ALTITUDE = 65;
const MAX_RECEIVER_ALTITUDE = 180;
let receiverAltitudeValue = rxDrone.position.y;
let receiverAltitudeTarget = 112;
let altitudeChangeRemaining = 2.5;

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

function chooseRandomTarget(position = rxDrone.position) {
    randomTarget.set(
        (Math.random() - 0.5) * 680,
        position.y,
        (Math.random() - 0.5) * 680
    );
    randomTargetValid = true;
}

function updateRandom(deltaTime, position = rxDrone.position) {
    if (!randomTargetValid) chooseRandomTarget(position);

    const dx = randomTarget.x - position.x;
    const dz = randomTarget.z - position.z;
    const distance = Math.hypot(dx, dz);

    if (distance < 10) {
        randomTargetValid = false;
        return;
    }

    const step = Math.min(110 * deltaTime, distance);
    position.x += (dx / distance) * step;
    position.z += (dz / distance) * step;
}

function beginPatternTransition() {
    patternTransition = {
        start: rxDrone.position.clone(),
        startVelocity: receiverVelocity.clone().clampLength(0, 180),
        pathPosition: rxDrone.position.clone(),
        elapsed: 0
    };
    simulationTime = 0;
    randomTargetValid = false;
    clearTrail();
}

function updatePatternPath(position, time) {
    if (pattern === "circular") {
        const angle = time * 0.55;
        const radius = 255;
        position.x = radius * Math.sin(angle);
        position.z = radius * (1 - Math.cos(angle));
    } else if (pattern === "figure8") {
        const angle = time * 0.5;
        position.x = 300 * Math.sin(angle);
        position.z = 190 * Math.sin(angle * 2);
    } else if (pattern === "straight") {
        position.x = 360 * Math.sin(time * 0.22);
        position.z = 0;
    }
}

function updateReceiverAltitude(deltaTime) {
    altitudeChangeRemaining -= deltaTime;
    if (altitudeChangeRemaining <= 0) {
        receiverAltitudeTarget = MIN_RECEIVER_ALTITUDE +
            Math.random() * (MAX_RECEIVER_ALTITUDE - MIN_RECEIVER_ALTITUDE);
        altitudeChangeRemaining = 3 + Math.random() * 5;
        recordActivity("ALTITUDE", `RX altitude target changed to ${(receiverAltitudeTarget * METERS_PER_WORLD_UNIT).toFixed(1)} m`);
    }

    // Smoothly drift toward a new random altitude instead of stepping vertically.
    const response = 1 - Math.exp(-0.55 * deltaTime);
    receiverAltitudeValue +=
        (receiverAltitudeTarget - receiverAltitudeValue) * response;
    return receiverAltitudeValue;
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
            recordActivity("PATTERN", `AUTO selected ${pattern}`);
            beginPatternTransition();
            return;
        }
    }

    simulationTime += deltaTime;
    const altitude = updateReceiverAltitude(deltaTime);
    if (patternTransition) {
        const transition = patternTransition;
        transition.elapsed += deltaTime;
        transition.pathPosition.y = altitude;
        if (pattern === "random") {
            updateRandom(deltaTime, transition.pathPosition);
        } else {
            updatePatternPath(transition.pathPosition, simulationTime);
        }

        const progress = THREE.MathUtils.clamp(
            transition.elapsed / PATTERN_TRANSITION_DURATION, 0, 1
        );
        const easedProgress = progress * progress * (3 - 2 * progress);
        const carriedPosition = transition.start.clone().addScaledVector(
            transition.startVelocity, transition.elapsed
        );
        rxDrone.position.lerpVectors(
            carriedPosition, transition.pathPosition, easedProgress
        );

        if (progress >= 1) {
            rxDrone.position.copy(transition.pathPosition);
            patternTransition = null;
        }
    } else {
        rxDrone.position.y = altitude;
        if (pattern === "random") {
            updateRandom(deltaTime);
        } else {
            updatePatternPath(rxDrone.position, simulationTime);
        }
    }
    enforceSeparation();
}

// ---- BEAM UPDATE ----

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

    // Photon shot noise: the received intensity fluctuates randomly each
    // frame, with a larger spread at higher levels.
    const shotNoise = disturbanceLevels.poisson > 0
        ? 1 - Math.random() * disturbanceLevels.poisson * 0.14
        : 1;

    beamCore.material.opacity =
        0.72 * fogAttenuation * rainAttenuation * speckle * shotNoise;

    beamGlow.material.opacity =
        0.13 * fogAttenuation * rainAttenuation * speckle * shotNoise;

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

// ---- UI ELEMENTS ----

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
const performanceButton = document.getElementById("performance-button");
const reportButton = document.getElementById("report-button");
const performanceModal = document.getElementById("performance-modal");
const performanceCloseButton = document.getElementById("performance-close");
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
const fpsDisplay = findElement("fps");
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
let performanceDuration = 0;
let performanceSampleClock = 0;
let performanceSamples = [];
let reportSamples = [];
const MAX_REPORT_SAMPLES = 7200;
const activityLog = [];
let activitySampleClock = 0;
let activityLastState = "";
let activityAlertState = new Set();
let currentFrameRate = 0;
let currentProcessingMs = 0;
let currentTrackingErrorPx = 0;
let totalTrackingErrorPx = 0;
let maxTrackingErrorPx = 0;
let trackedFrames = 0;
let lockedFrames = 0;
const MAX_PERFORMANCE_SAMPLES = 120;
const MAX_ACTIVITY_LOG = 3000;

function recordActivity(event, details, level = "INFO") {
    const time = new Date().toLocaleTimeString();
    activityLog.push({ time, event, details: String(details), level });
    if (activityLog.length > MAX_ACTIVITY_LOG) activityLog.splice(0, 500);
    if (event !== "SAMPLE") renderActivityPanel();
}

const ACTIVITY_PANEL_LIMIT = 150;

function renderActivityPanel() {
    const list = document.getElementById("activity-panel-list");
    if (!list) return;
    const events = activityLog.filter((entry) => entry.event !== "SAMPLE");
    const counter = document.getElementById("activity-panel-count");
    if (counter) counter.textContent = String(events.length);
    list.textContent = "";
    if (!events.length) {
        const empty = document.createElement("li");
        empty.className = "activity-empty";
        empty.textContent = "No events yet. Press START.";
        list.appendChild(empty);
        return;
    }
    events.slice(-ACTIVITY_PANEL_LIMIT).reverse().forEach((entry) => {
        const item = document.createElement("li");
        if (entry.level === "ALERT") item.className = "level-alert";
        const meta = document.createElement("div");
        meta.className = "activity-meta";
        const name = document.createElement("span");
        name.className = "activity-event";
        name.textContent = entry.event;
        const time = document.createElement("span");
        time.textContent = entry.time;
        meta.append(name, time);
        const details = document.createElement("span");
        details.className = "activity-details";
        details.textContent = entry.details;
        item.append(meta, details);
        list.appendChild(item);
    });
    list.scrollTop = 0;
}

function sampleBeaconActivity() {
    const status = trackingStatus?.textContent?.trim() || "UNKNOWN";
    if (status !== activityLastState) {
        recordActivity("TRACKING", `Beacon state changed to ${status}`);
        activityLastState = status;
    }
    const lock = trackedFrames ? lockedFrames / trackedFrames * 100 : 100;
    const distanceMeters = rxDrone.position.distanceTo(txDrone.position) * METERS_PER_WORLD_UNIT;
    const fpsLow = currentFrameRate > 0 && currentFrameRate < 30;
    const lockLow = trackedFrames > 0 && lock < 95;
    const separationLow = distanceMeters < 5;
    const checks = [
        ["fps", fpsLow, `Frame rate ${currentFrameRate.toFixed(1)} FPS (reference 30 FPS)`],
        ["lock", lockLow, `Lock retention ${lock.toFixed(1)}% (reference 95%)`],
        ["separation", separationLow, `TX/RX separation ${distanceMeters.toFixed(2)} m (reference 5 m)`]
    ];
    checks.forEach(([key, violated, details]) => {
        if (violated && !activityAlertState.has(key)) recordActivity("THRESHOLD ALERT", details, "ALERT");
        if (!violated && activityAlertState.has(key)) recordActivity("THRESHOLD CLEARED", `${key.toUpperCase()} back within reference`);
        if (violated) activityAlertState.add(key); else activityAlertState.delete(key);
    });
    const disturbanceText = Object.entries(disturbanceLevels).map(([name, value]) => `${name}=${value}`).join(", ");
    recordActivity("SAMPLE", `pattern=${pattern}; state=${status}; altitude=${(rxDrone.position.y * METERS_PER_WORLD_UNIT).toFixed(1)}m; separation=${distanceMeters.toFixed(1)}m; decoys=${decoyDrones.filter((drone) => drone.visible).length}; disturbances=${disturbanceText}; confidence=${confidenceDisplay?.textContent || "--"}; error=${errorDisplay?.textContent || "--"}; fps=${currentFrameRate.toFixed(1)}`);
}

const REPORT_REQUIREMENTS = { fps: 30, lock: 95, targetLoss: 5, processingMs: 1000 / 30, separation: 5 };

function reportSeriesStats(field) {
    const values = reportSamples.map((sample) => sample[field]).filter((value) => Number.isFinite(value));
    if (!values.length) return null;
    return {
        min: Math.min(...values),
        max: Math.max(...values),
        mean: values.reduce((sum, value) => sum + value, 0) / values.length
    };
}

function buildTechnicalReportPdf() {
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    const reportId = `FSOC-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    const lock = trackedFrames ? lockedFrames / trackedFrames * 100 : null;
    const targetLoss = lock === null ? null : 100 - lock;
    const meanError = trackedFrames ? totalTrackingErrorPx / trackedFrames : null;
    const distanceMeters = rxDrone.position.distanceTo(txDrone.position) * METERS_PER_WORLD_UNIT;
    const fpsStats = reportSeriesStats("fps");
    const errorStats = reportSeriesStats("error");
    const lockStats = reportSeriesStats("lock");
    const processingStats = reportSeriesStats("processing");
    const mainEvents = activityLog.filter((entry) => entry.event !== "SAMPLE");
    const sampleCount = activityLog.length - mainEvents.length;
    const fmt = (value, digits, unit) => (value === null || value === undefined || !Number.isFinite(value)) ? "--" : `${value.toFixed(digits)}${unit}`;
    const trackingChanges = mainEvents.filter((entry) => entry.event === "TRACKING").length;
    const parameterChanges = mainEvents.filter((entry) => ["PARAMETER", "PATTERN", "ALTITUDE", "BEACON"].includes(entry.event)).length;

    const metricRows = [
        ["Mean frame rate", fmt(fpsStats?.mean, 1, " FPS"), "30 Hz update rate"],
        ["Frame rate range", fpsStats ? `${fpsStats.min.toFixed(1)} - ${fpsStats.max.toFixed(1)} FPS` : "--", "30 Hz update rate"],
        ["Lock retention", fmt(lock, 2, " %"), "95 %"],
        ["Target loss", fmt(targetLoss, 2, " %"), "5 %"],
        ["Acquisition time", fmt(acquisitionTime, 2, " s"), "Logged"],
        ["Re-acquisition time", fmt(reacquisitionTime, 2, " s"), "Logged"],
        ["Mean tracking error", fmt(meanError, 2, " px"), "Logged"],
        ["Max tracking error", trackedFrames ? `${maxTrackingErrorPx.toFixed(2)} px` : "--", "Logged"],
        ["Mean processing time", fmt(processingStats?.mean, 2, " ms"), "33.3 ms frame budget"],
        ["TX/RX separation", `${distanceMeters.toFixed(1)} m`, "5 m minimum spacing"]
    ];
    const hasRun = performanceDuration > 0 && reportSamples.length > 0;
    // Neutral names for the log's threshold events.
    const reportEventName = (event) => ({ "THRESHOLD ALERT": "REFERENCE CROSSED", "THRESHOLD CLEARED": "REFERENCE RESTORED" })[event] || event;

    // ---- Minimal PDF writer (A4, standard Type1 fonts, vector graphics) ----
    const PAGE_W = 595, PAGE_H = 842, MARGIN = 50, CONTENT_W = PAGE_W - MARGIN * 2;
    const TOP = PAGE_H - 70, BOTTOM = 60;
    const FONTS = { regular: "F1", bold: "F2", mono: "F3" };
    const WIDTH_FACTOR = { regular: 0.5, bold: 0.55, mono: 0.6 };
    const pages = [];
    let ops = null;
    let y = TOP;

    const toAscii = (value) => String(value)
        .replace(/°/g, " deg").replace(/×/g, "x").replace(/≥/g, ">=").replace(/≤/g, "<=").replace(/[–—]/g, "-")
        .normalize("NFKD").replace(/[^\x20-\x7E]/g, "");
    const esc = (value) => toAscii(value).replace(/([\\()])/g, "\\$1");
    const rgb = (hex) => [1, 3, 5].map((i) => (parseInt(hex.slice(i, i + 2), 16) / 255).toFixed(3)).join(" ");
    const textWidth = (value, font, size) => toAscii(value).length * size * WIDTH_FACTOR[font];

    function text(x, yy, value, { font = "regular", size = 10, color = "#1a2733", align = "left" } = {}) {
        let tx = x;
        if (align === "right") tx = x - textWidth(value, font, size);
        if (align === "center") tx = x - textWidth(value, font, size) / 2;
        ops.push(`BT ${rgb(color)} rg /${FONTS[font]} ${size} Tf ${tx.toFixed(2)} ${yy.toFixed(2)} Td (${esc(value)}) Tj ET`);
    }
    function rect(x, yy, w, h, fill, stroke) {
        if (fill) ops.push(`${rgb(fill)} rg ${x.toFixed(2)} ${yy.toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re f`);
        if (stroke) ops.push(`${rgb(stroke)} RG 0.6 w ${x.toFixed(2)} ${yy.toFixed(2)} ${w.toFixed(2)} ${h.toFixed(2)} re S`);
    }
    function line(x1, y1, x2, y2, color = "#c5d2dc", width = 0.6, dash = null) {
        ops.push(`${dash ? `[${dash}] 0 d ` : ""}${rgb(color)} RG ${width} w ${x1.toFixed(2)} ${y1.toFixed(2)} m ${x2.toFixed(2)} ${y2.toFixed(2)} l S${dash ? " [] 0 d" : ""}`);
    }
    function wrap(value, font, size, maxWidth) {
        const words = toAscii(value).split(/\s+/).filter(Boolean);
        const maxChars = Math.max(4, Math.floor(maxWidth / (size * WIDTH_FACTOR[font])));
        const out = [];
        let current = "";
        words.forEach((word) => {
            while (word.length > maxChars) {
                if (current) { out.push(current); current = ""; }
                out.push(word.slice(0, maxChars));
                word = word.slice(maxChars);
            }
            if (current && (current.length + 1 + word.length) > maxChars) {
                out.push(current);
                current = word;
            } else {
                current = current ? `${current} ${word}` : word;
            }
        });
        if (current) out.push(current);
        return out.length ? out : [""];
    }
    function newPage() {
        ops = [];
        pages.push(ops);
        y = TOP;
    }
    function ensureSpace(height) {
        if (y - height < BOTTOM) newPage();
    }
    function heading(number, title) {
        ensureSpace(110);
        y -= 12;
        text(MARGIN, y, `${number}. ${title.toUpperCase()}`, { font: "bold", size: 12, color: "#0b4f6c" });
        y -= 6;
        line(MARGIN, y, MARGIN + CONTENT_W, y, "#1b9fc4", 1);
        y -= 16;
    }
    function paragraph(value, { size = 10, font = "regular", color = "#1a2733", gap = 6 } = {}) {
        wrap(value, font, size, CONTENT_W).forEach((ln) => {
            ensureSpace(size + 4);
            text(MARGIN, y, ln, { font, size, color });
            y -= size + 4;
        });
        y -= gap;
    }
    function bullet(value) {
        wrap(value, "regular", 10, CONTENT_W - 16).forEach((ln, index) => {
            ensureSpace(14);
            if (index === 0) text(MARGIN + 4, y, "-", { size: 10, color: "#1b9fc4", font: "bold" });
            text(MARGIN + 16, y, ln, { size: 10 });
            y -= 14;
        });
        y -= 2;
    }
    function table(columns, rows, { size = 9, font = "regular" } = {}) {
        const totalWeight = columns.reduce((sum, column) => sum + column.weight, 0);
        const widths = columns.map((column) => CONTENT_W * column.weight / totalWeight);
        const lineHeight = size + 3;
        const drawHeader = () => {
            ensureSpace(40);
            rect(MARGIN, y - 6, CONTENT_W, 18, "#0b4f6c");
            let x = MARGIN;
            columns.forEach((column, index) => {
                text(x + 5, y, column.label, { font: "bold", size: 8.5, color: "#ffffff" });
                x += widths[index];
            });
            y -= 18;
        };
        drawHeader();
        rows.forEach((row, rowIndex) => {
            const cellLines = row.map((cell, index) => wrap(cell, font, size, widths[index] - 10));
            const height = Math.max(...cellLines.map((lines) => lines.length)) * lineHeight + 6;
            if (y - height < BOTTOM) { newPage(); drawHeader(); }
            const top = y + lineHeight - 2;
            rect(MARGIN, top - height, CONTENT_W, height, rowIndex % 2 ? "#f3f7fa" : "#ffffff");
            line(MARGIN, top - height, MARGIN + CONTENT_W, top - height, "#dbe4ea", 0.4);
            let x = MARGIN;
            cellLines.forEach((lines, index) => {
                lines.forEach((ln, i) => text(x + 5, y - i * lineHeight, ln, { font, size }));
                x += widths[index];
            });
            y -= height;
        });
        y -= 10;
    }
    function chart(x, top, w, h, title, unit, field, color, threshold) {
        const samples = reportSamples.filter((sample) => Number.isFinite(sample[field]));
        rect(x, top - h, w, h, "#fbfdfe", "#c5d2dc");
        text(x + 8, top - 14, title, { font: "bold", size: 9, color: "#0b4f6c" });
        text(x + w - 8, top - 14, unit, { size: 7, color: "#6b7c8a", align: "right" });
        const plot = { left: x + 36, right: x + w - 10, top: top - 24, bottom: top - h + 20 };
        if (samples.length < 2) {
            text(x + w / 2, top - h / 2, "Insufficient samples - run the simulation", { size: 8, color: "#6b7c8a", align: "center" });
            return;
        }
        const values = samples.map((sample) => sample[field]);
        let minV = Math.min(...values, threshold ?? Infinity);
        let maxV = Math.max(...values, threshold ?? -Infinity);
        if (maxV - minV < 1e-6) { maxV += 1; minV -= 1; }
        const padV = (maxV - minV) * 0.1;
        minV = Math.max(0, minV - padV);
        maxV += padV;
        const t0 = samples[0].time, t1 = samples[samples.length - 1].time;
        const sx = (t) => plot.left + (plot.right - plot.left) * ((t - t0) / Math.max(t1 - t0, 1e-6));
        const sy = (v) => plot.bottom + (plot.top - plot.bottom) * ((v - minV) / (maxV - minV));
        for (let i = 0; i <= 4; i++) {
            const v = minV + (maxV - minV) * i / 4;
            const gy = sy(v);
            line(plot.left, gy, plot.right, gy, "#e3eaef", 0.4);
            text(plot.left - 4, gy - 2.5, v >= 100 ? v.toFixed(0) : v.toFixed(1), { size: 6.5, color: "#6b7c8a", align: "right" });
        }
        line(plot.left, plot.bottom, plot.right, plot.bottom, "#8fa3b3", 0.6);
        line(plot.left, plot.bottom, plot.left, plot.top, "#8fa3b3", 0.6);
        text(plot.left, plot.bottom - 11, `${t0.toFixed(0)} s`, { size: 6.5, color: "#6b7c8a" });
        text(plot.right, plot.bottom - 11, `${t1.toFixed(0)} s`, { size: 6.5, color: "#6b7c8a", align: "right" });
        text((plot.left + plot.right) / 2, plot.bottom - 11, "simulation time", { size: 6.5, color: "#6b7c8a", align: "center" });
        if (threshold !== null && threshold !== undefined) {
            line(plot.left, sy(threshold), plot.right, sy(threshold), "#8fa3b3", 0.7, "3 2");
        }
        const step = Math.max(1, Math.ceil(samples.length / 400));
        const path = [];
        for (let i = 0; i < samples.length; i += step) {
            path.push(`${sx(samples[i].time).toFixed(2)} ${sy(samples[i][field]).toFixed(2)} ${path.length ? "l" : "m"}`);
        }
        ops.push(`${rgb(color)} RG 1 w 1 j ${path.join(" ")} S`);
    }

    // ---- Cover block ----
    newPage();
    rect(0, PAGE_H - 170, PAGE_W, 170, "#071a2c");
    rect(0, PAGE_H - 174, PAGE_W, 4, "#1b9fc4");
    text(MARGIN, PAGE_H - 62, "FSOC COARSE ALIGNMENT SYSTEM", { font: "bold", size: 11, color: "#35d8ff" });
    text(MARGIN, PAGE_H - 92, "Technical Performance Report", { font: "bold", size: 24, color: "#ffffff" });
    text(MARGIN, PAGE_H - 114, "3D airspace beacon tracking simulation - run summary and measurements", { size: 10, color: "#a9c3d6" });
    text(MARGIN, PAGE_H - 145, `Report ID: ${reportId}`, { font: "mono", size: 8.5, color: "#a9c3d6" });
    text(MARGIN + 190, PAGE_H - 145, `Generated: ${now.toLocaleString()}`, { font: "mono", size: 8.5, color: "#a9c3d6" });
    text(PAGE_W - MARGIN, PAGE_H - 145, `Duration: ${performanceDuration.toFixed(1)} s`, { font: "mono", size: 8.5, color: "#a9c3d6", align: "right" });
    y = PAGE_H - 205;

    // Key figures strip
    const keyFigures = [
        ["DURATION", `${performanceDuration.toFixed(1)} s`],
        ["MEAN FRAME RATE", fmt(fpsStats?.mean, 1, " FPS")],
        ["LOCK RETENTION", fmt(lock, 1, " %")],
        ["MEAN ERROR", fmt(meanError, 2, " px")]
    ];
    const cellW = CONTENT_W / keyFigures.length;
    rect(MARGIN, y - 34, CONTENT_W, 46, "#f3f7fa", "#c5d2dc");
    keyFigures.forEach(([label, value], index) => {
        const cx = MARGIN + cellW * index;
        if (index > 0) line(cx, y - 28, cx, y + 6, "#dbe4ea", 0.6);
        text(cx + 14, y - 4, label, { font: "bold", size: 7.5, color: "#6b7c8a" });
        text(cx + 14, y - 22, value, { font: "bold", size: 13, color: "#0b4f6c" });
    });
    y -= 62;

    heading(1, "Simulation Overview");
    paragraph(!hasRun
        ? "No simulation data had been recorded when this report was generated. Start the simulation and allow it to run to populate the measurements below."
        : `The 3D airspace simulation ran for ${performanceDuration.toFixed(1)} s with the receiver following the ${autoMode ? `AUTO (${pattern})` : pattern} movement pattern `
          + `at ${(rxDrone.position.y * METERS_PER_WORLD_UNIT).toFixed(1)} m altitude, ${distanceMeters.toFixed(1)} m from the transmitter, with ${decoyDrones.filter((drone) => drone.visible).length} decoy beacon(s) in the scene. `
          + `The tracker ran at a mean of ${fmt(fpsStats?.mean, 1, " FPS")} and held lock on the beacon for ${fmt(lock, 1, " %")} of evaluated frames. `
          + `Mean tracking error was ${fmt(meanError, 2, " px")}${trackedFrames ? ` (peak ${maxTrackingErrorPx.toFixed(2)} px)` : ""}. `
          + `${mainEvents.length} event(s) were logged during the run, including ${trackingChanges} tracking state change(s) and ${parameterChanges} configuration change(s).`);

    heading(2, "Test Configuration");
    table(
        [{ label: "PARAMETER", weight: 1 }, { label: "VALUE", weight: 1.6 }],
        [
            ["Movement pattern", autoMode ? `AUTO (current: ${pattern})` : pattern],
            ["Beacon visibility", beaconHidden ? "Hidden (prediction only)" : "Visible"],
            ["Visible decoy beacons", String(decoyDrones.filter((drone) => drone.visible).length)],
            ["Environmental disturbances", Object.entries(disturbanceLevels).map(([name, value]) => `${name}: ${value}/4`).join(", ")],
            ["RX altitude", `${(rxDrone.position.y * METERS_PER_WORLD_UNIT).toFixed(1)} m`],
            ["TX/RX separation", `${distanceMeters.toFixed(1)} m`],
            ["Final tracking state", trackingStatus?.textContent?.trim() || "UNKNOWN"],
            ["Performance samples recorded", `${reportSamples.length} (every 0.5 s)`],
            ["Frames evaluated", String(trackedFrames)]
        ]
    );

    heading(3, "Performance Metrics");
    paragraph("Measured values for the run. The reference column lists the corresponding figures from the supplied evaluation criteria for context.", { size: 9, color: "#4a5b68" });
    table(
        [{ label: "METRIC", weight: 1.3 }, { label: "MEASURED", weight: 1.1 }, { label: "REFERENCE", weight: 1.2 }],
        metricRows
    );

    heading(4, "Time-Series Analysis");
    paragraph("Dashed lines mark the reference values.", { size: 9, color: "#4a5b68" });
    const chartW = (CONTENT_W - 14) / 2, chartH = 160;
    ensureSpace(chartH * 2 + 20);
    chart(MARGIN, y, chartW, chartH, "FRAME RATE", "FPS", "fps", "#1b9fc4", REPORT_REQUIREMENTS.fps);
    chart(MARGIN + chartW + 14, y, chartW, chartH, "TRACKING ERROR", "pixels", "error", "#e0603f", null);
    y -= chartH + 12;
    chart(MARGIN, y, chartW, chartH, "LOCK RETENTION", "%", "lock", "#138a52", REPORT_REQUIREMENTS.lock);
    chart(MARGIN + chartW + 14, y, chartW, chartH, "PROCESSING TIME", "ms / frame", "processing", "#7a5cd6", REPORT_REQUIREMENTS.processingMs);
    y -= chartH + 20;
    table(
        [{ label: "SERIES", weight: 1.3 }, { label: "MIN", weight: 1 }, { label: "MEAN", weight: 1 }, { label: "MAX", weight: 1 }],
        [
            ["Frame rate (FPS)", fpsStats, 1],
            ["Tracking error (px)", errorStats, 2],
            ["Lock retention (%)", lockStats, 1],
            ["Processing time (ms)", processingStats, 2]
        ].map(([name, stats, digits]) => [name, fmt(stats?.min, digits, ""), fmt(stats?.mean, digits, ""), fmt(stats?.max, digits, "")])
    );

    heading(5, "Event Log");
    paragraph(`${mainEvents.length} event(s) recorded. The ${sampleCount} periodic telemetry samples are summarised in Sections 3 and 4 rather than listed here.${mainEvents.length > 400 ? " The most recent 400 events are shown." : ""}`, { size: 9, color: "#4a5b68" });
    if (mainEvents.length) {
        table(
            [{ label: "TIME", weight: 0.75 }, { label: "EVENT", weight: 1.15 }, { label: "DETAILS", weight: 3.4 }],
            mainEvents.slice(-400).map((entry) => [entry.time, reportEventName(entry.event), entry.details]),
            { size: 8 }
        );
    } else {
        paragraph("No events recorded.", { size: 9, color: "#6b7c8a" });
    }

    heading(6, "Observations");
    const observations = [];
    if (!hasRun) {
        observations.push("No run data is available yet.");
    } else {
        if (fpsStats) observations.push(`Frame rate ranged from ${fpsStats.min.toFixed(1)} to ${fpsStats.max.toFixed(1)} FPS across ${reportSamples.length} samples.`);
        if (lock !== null) observations.push(`The beacon was held in lock for ${lock.toFixed(1)} % of ${trackedFrames} evaluated frames.`);
        if (acquisitionTime !== null) observations.push(`Initial acquisition took ${acquisitionTime.toFixed(2)} s${reacquisitionTime !== null ? `; the most recent re-acquisition took ${reacquisitionTime.toFixed(2)} s` : ""}.`);
        if (errorStats) observations.push(`Tracking error averaged ${errorStats.mean.toFixed(2)} px over the sampled period, between ${errorStats.min.toFixed(2)} and ${errorStats.max.toFixed(2)} px.`);
        if (processingStats) observations.push(`Per-frame processing averaged ${processingStats.mean.toFixed(2)} ms.`);
        const activeDisturbances = Object.entries(disturbanceLevels).filter(([, value]) => value > 0);
        observations.push(activeDisturbances.length
            ? `Active disturbances at report time: ${activeDisturbances.map(([name, value]) => `${name} ${value}/4`).join(", ")}.`
            : "No environmental disturbances were active at report time.");
    }
    observations.forEach(bullet);

    // ---- Page furniture ----
    pages.forEach((pageOps, index) => {
        ops = pageOps;
        if (index > 0) {
            text(MARGIN, PAGE_H - 38, "FSOC Coarse Alignment - Technical Performance Report", { size: 8, color: "#6b7c8a" });
            text(PAGE_W - MARGIN, PAGE_H - 38, reportId, { font: "mono", size: 8, color: "#6b7c8a", align: "right" });
            line(MARGIN, PAGE_H - 45, PAGE_W - MARGIN, PAGE_H - 45, "#c5d2dc", 0.5);
        }
        line(MARGIN, 42, PAGE_W - MARGIN, 42, "#c5d2dc", 0.5);
        text(MARGIN, 30, "Generated by FSOC Optical Link Control Center (simulation mode)", { size: 7.5, color: "#6b7c8a" });
        text(PAGE_W - MARGIN, 30, `Page ${index + 1} of ${pages.length}`, { size: 7.5, color: "#6b7c8a", align: "right" });
    });

    // ---- Serialize (all content is ASCII, so string length == byte length) ----
    const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        null,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        `<< /Title (${esc(`FSOC Technical Performance Report ${reportId}`)}) /Producer (FSOC Control Center) >>`
    ];
    const pageRefs = [];
    pages.forEach((pageOps) => {
        const stream = pageOps.join("\n");
        const pageId = objects.length + 1;
        objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_W} ${PAGE_H}] /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> /Contents ${pageId + 1} 0 R >>`);
        objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
        pageRefs.push(`${pageId} 0 R`);
    });
    objects[1] = `<< /Type /Pages /Kids [${pageRefs.join(" ")}] /Count ${pageRefs.length} >>`;
    let pdf = "%PDF-1.4\n";
    const offsets = [];
    objects.forEach((body, index) => {
        offsets.push(pdf.length);
        pdf += `${index + 1} 0 obj\n${body}\nendobj\n`;
    });
    const xrefOffset = pdf.length;
    pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    offsets.forEach((offset) => { pdf += `${String(offset).padStart(10, "0")} 00000 n \n`; });
    pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
    return { pdf, fileName: `fsoc-technical-report-${reportId.slice(5)}.pdf` };
}

const reportToast = document.getElementById("report-toast");
let reportToastTimer = null;
let reportConfirmTimer = null;

function showReportToast(title, detail, isError = false) {
    if (!reportToast) return;
    document.getElementById("report-toast-title").textContent = title;
    document.getElementById("report-toast-detail").textContent = detail;
    reportToast.classList.toggle("is-error", isError);
    reportToast.querySelector(".report-toast-icon").textContent = isError ? "!" : "✓";
    reportToast.hidden = false;
    clearTimeout(reportToastTimer);
    reportToastTimer = setTimeout(() => { reportToast.hidden = true; }, isError ? 7000 : 5000);
}

// Called by the Qt host (main.py) once the download has been written to disk.
window.fsocReportDownloaded = (path, ok) => {
    clearTimeout(reportConfirmTimer);
    if (ok) {
        showReportToast("REPORT DOWNLOADED", `Opening ${path.split(/[\\/]/).pop()} (saved in Downloads)`);
        recordActivity("REPORT", `Technical report saved to ${path}`);
    } else {
        showReportToast("DOWNLOAD FAILED", "The report could not be saved. Check disk space and folder permissions.", true);
    }
};

function downloadPerformanceReport() {
    updatePerformanceSummary();
    let result;
    try {
        result = buildTechnicalReportPdf();
    } catch (error) {
        console.error(error);
        showReportToast("REPORT FAILED", error?.message || String(error), true);
        return;
    }
    const url = URL.createObjectURL(new Blob([result.pdf], { type: "application/pdf" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = result.fileName;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);

    if (/QtWebEngine/i.test(navigator.userAgent)) {
        // The Qt host confirms with the saved path; flag it if it never does.
        clearTimeout(reportConfirmTimer);
        reportConfirmTimer = setTimeout(() => {
            showReportToast("DOWNLOAD NOT CONFIRMED", `${result.fileName} may not have been saved.`, true);
        }, 10000);
    } else {
        showReportToast("REPORT DOWNLOADED", `${result.fileName} saved to your Downloads folder`);
        recordActivity("REPORT", `Technical report ${result.fileName} downloaded`);
    }
}

reportButton?.addEventListener("click", downloadPerformanceReport);

function updatePerformanceSummary() {
    const averageError = trackedFrames
        ? totalTrackingErrorPx / trackedFrames
        : null;
    const lockRetention = trackedFrames
        ? lockedFrames / trackedFrames * 100
        : null;
    const values = {
        "metric-duration": `${performanceDuration.toFixed(1)} s`,
        "metric-fps": currentFrameRate > 0
            ? `${currentFrameRate.toFixed(1)} FPS`
            : "-- FPS",
        "metric-acquisition": acquisitionTime === null
            ? "-- s"
            : `${acquisitionTime.toFixed(2)} s`,
        "metric-reacquisition": reacquisitionTime === null
            ? "-- s"
            : `${reacquisitionTime.toFixed(2)} s`,
        "metric-mean-error": averageError === null
            ? "-- px"
            : `${averageError.toFixed(2)} px`,
        "metric-max-error": trackedFrames
            ? `${maxTrackingErrorPx.toFixed(2)} px`
            : "-- px",
        "metric-lock-retention": lockRetention === null
            ? "--%"
            : `${lockRetention.toFixed(1)}%`,
        "metric-processing": performanceDuration > 0
            ? `${currentProcessingMs.toFixed(2)} ms`
            : "-- ms"
    };

    Object.entries(values).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });
}

function drawPerformanceChart(canvasId, field, color, options = {}) {
    const chart = document.getElementById(canvasId);
    const context = chart?.getContext("2d");
    if (!chart || !context) return;

    const width = chart.clientWidth || 600;
    const height = chart.clientHeight || 150;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    chart.width = Math.round(width * pixelRatio);
    chart.height = Math.round(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    context.clearRect(0, 0, width, height);

    const margin = { left: 42, right: 12, top: 10, bottom: 24 };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const values = performanceSamples
        .map((sample) => sample[field])
        .filter(Number.isFinite);
    const maxValue = options.max ?? Math.max(
        options.minimumMax || 1,
        ...values.map((value) => value * 1.15)
    );

    context.font = "10px Arial, sans-serif";
    context.lineWidth = 1;
    for (let tick = 0; tick <= 4; tick++) {
        const ratio = tick / 4;
        const y = margin.top + plotHeight * ratio;
        context.strokeStyle = "rgba(113, 144, 173, 0.18)";
        context.beginPath();
        context.moveTo(margin.left, y);
        context.lineTo(width - margin.right, y);
        context.stroke();
        context.fillStyle = "#7190ad";
        context.textAlign = "right";
        context.fillText((maxValue * (1 - ratio)).toFixed(0), margin.left - 7, y + 3);
    }

    const yFor = (value) =>
        margin.top + plotHeight * (1 - THREE.MathUtils.clamp(value / maxValue, 0, 1));

    if (Number.isFinite(options.threshold)) {
        const thresholdY = yFor(options.threshold);
        context.save();
        context.setLineDash([5, 4]);
        context.strokeStyle = "rgba(255, 200, 87, 0.8)";
        context.beginPath();
        context.moveTo(margin.left, thresholdY);
        context.lineTo(width - margin.right, thresholdY);
        context.stroke();
        context.restore();
    }

    if (performanceSamples.length === 0) {
        context.fillStyle = "#7190ad";
        context.textAlign = "center";
        context.fillText("Start simulation to collect live data", width / 2, height / 2);
    } else {
        context.strokeStyle = color;
        context.lineWidth = 2;
        context.lineJoin = "round";
        context.beginPath();
        performanceSamples.forEach((sample, index) => {
            const x = margin.left + (performanceSamples.length > 1
                ? plotWidth * index / (performanceSamples.length - 1)
                : plotWidth);
            const y = yFor(sample[field]);
            if (index === 0) context.moveTo(x, y);
            else context.lineTo(x, y);
        });
        context.stroke();
    }

    const firstTime = performanceSamples[0]?.time ?? 0;
    const lastTime = performanceSamples[performanceSamples.length - 1]?.time ?? 0;
    context.fillStyle = "#7190ad";
    context.textAlign = "left";
    context.fillText(`-${Math.max(0, lastTime - firstTime).toFixed(0)} s`, margin.left, height - 5);
    context.textAlign = "right";
    context.fillText("now", width - margin.right, height - 5);
}

function drawPerformanceDashboard() {
    updatePerformanceSummary();
    drawPerformanceChart("chart-fps", "fps", "#35d8ff", {
        minimumMax: 35,
        threshold: 30
    });
    drawPerformanceChart("chart-error", "error", "#ff8b74", {
        minimumMax: 10
    });
    drawPerformanceChart("chart-lock", "lock", "#42f5a7", {
        max: 100,
        threshold: 95
    });
    drawPerformanceChart("chart-processing", "processing", "#b59aff", {
        minimumMax: 5
    });
}

function samplePerformance() {
    const lockRetention = trackedFrames
        ? lockedFrames / trackedFrames * 100
        : 0;
    performanceSamples.push({
        time: performanceDuration,
        fps: currentFrameRate,
        error: currentTrackingErrorPx,
        lock: lockRetention,
        processing: currentProcessingMs
    });
    if (performanceSamples.length > MAX_PERFORMANCE_SAMPLES) {
        performanceSamples.shift();
    }
    reportSamples.push(performanceSamples[performanceSamples.length - 1]);
    if (reportSamples.length > MAX_REPORT_SAMPLES) reportSamples.splice(0, reportSamples.length - MAX_REPORT_SAMPLES);
    updatePerformanceSummary();
    if (performanceModal && !performanceModal.hidden) {
        drawPerformanceDashboard();
    }
}

function closePerformanceModal() {
    if (!performanceModal) return;
    performanceModal.hidden = true;
    performanceButton?.focus();
}

performanceButton?.addEventListener("click", () => {
    if (!performanceModal) return;
    performanceModal.hidden = false;
    drawPerformanceDashboard();
    performanceCloseButton?.focus();
});
performanceCloseButton?.addEventListener("click", closePerformanceModal);
performanceModal?.addEventListener("click", (event) => {
    if (event.target === performanceModal) closePerformanceModal();
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && performanceModal && !performanceModal.hidden) {
        closePerformanceModal();
    }
});

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
    recordActivity("BEACON", beaconHidden ? "Beacon hidden; receiver path remains active" : "Beacon visible; tracking can reacquire target");
}

// ---- AIRCRAFT HUD ----

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

// ---- TRACKING AND TELEMETRY ----

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
    currentTrackingErrorPx = error * 0.3;
    if (running) {
        trackedFrames++;
        totalTrackingErrorPx += currentTrackingErrorPx;
        maxTrackingErrorPx = Math.max(maxTrackingErrorPx, currentTrackingErrorPx);
        if (detected || recentlyTracked) lockedFrames++;
    }

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

// ---- BUTTONS AND PATTERN SELECTION ----

if (startButton) {
    startButton.addEventListener("click", () => {
        running = true;
        recordActivity("SIMULATION", "Simulation started");
    });
}

if (pauseButton) {
    pauseButton.addEventListener("click", () => {
        running = false;
        recordActivity("SIMULATION", "Simulation paused");
    });
}

function resetSimulation() {
    running = false;
    setBeaconHidden(false);
    rxDrone.position.set(0, 80, 0);
    receiverAltitudeValue = rxDrone.position.y;
    receiverAltitudeTarget = receiverAltitudeValue;
    altitudeChangeRemaining = 1.5;
    kalmanPosition.copy(beacon.getWorldPosition(new THREE.Vector3()));
    kalmanVelocity.set(0, 0, 0);
    trackingLostTime = 0;
    trackingElapsed = 0;
    acquisitionTime = null;
    reacquisitionTime = null;
    reacquisitionStarted = null;
    performanceDuration = 0;
    performanceSampleClock = 0;
    performanceSamples = [];
    reportSamples = [];
    currentFrameRate = 0;
    currentProcessingMs = 0;
    currentTrackingErrorPx = 0;
    totalTrackingErrorPx = 0;
    maxTrackingErrorPx = 0;
    trackedFrames = 0;
    lockedFrames = 0;
    updatePerformanceSummary();
    if (performanceModal && !performanceModal.hidden) {
        drawPerformanceDashboard();
    }
    rxDrone.rotation.y = 0;
    simulationTime = 0;
    patternTransition = null;
    receiverVelocity.set(0, 0, 0);
    previousReceiverPosition.copy(rxDrone.position);
    randomTargetValid = false;
    clearTrail();
    updateBeam(0);
    recordActivity("SIMULATION", "Simulation reset; performance counters cleared");
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
    beginPatternTransition();

    if (patternSelect && patternSelect.value !== selected) {
        patternSelect.value = selected;
    }
    clearTrail();
    recordActivity("PATTERN", `Receiver movement pattern changed to ${autoMode ? `AUTO (starting ${pattern})` : pattern}`);
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
    recordActivity("PARAMETER", `Visible decoy beacon count set to ${count}`);
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

// ---- PYTHON / DASHBOARD API ----

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

// ---- ANIMATION LOOP ----

let previousTime = performance.now() / 1000;
let elapsedTime = 0;
function animate(now) {
    requestAnimationFrame(animate);
    const frameStartedAt = performance.now();

    const currentTime = now / 1000;
    const deltaTime = Math.min(
        Math.max(currentTime - previousTime, 0),
        0.05
    );

    previousTime = currentTime;
    elapsedTime += deltaTime;

    if (running) {
        performanceDuration += deltaTime;
        updatePattern(deltaTime);
        updateTrail();
        activitySampleClock += deltaTime;
        if (activitySampleClock >= 1) {
            activitySampleClock %= 1;
            sampleBeaconActivity();
        }
    }

    if (deltaTime > 1e-5) {
        receiverVelocity.copy(rxDrone.position)
            .sub(previousReceiverPosition)
            .divideScalar(deltaTime);
    } else {
        receiverVelocity.set(0, 0, 0);
    }
    previousReceiverPosition.copy(rxDrone.position);

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
    renderer.render(scene, camera);
    drawAircraftOverlay();

    currentProcessingMs = performance.now() - frameStartedAt;
    if (deltaTime > 1e-5) {
        const instantaneousFps = 1 / deltaTime;
        currentFrameRate = currentFrameRate === 0
            ? instantaneousFps
            : currentFrameRate * 0.85 + instantaneousFps * 0.15;
    }
    if (fpsDisplay && currentFrameRate > 0) {
        fpsDisplay.textContent = String(Math.round(currentFrameRate));
    }

    if (running) {
        performanceSampleClock += deltaTime;
        if (performanceSampleClock >= 0.5) {
            performanceSampleClock %= 0.5;
            samplePerformance();
        }
    }
}

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
