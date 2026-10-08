import { Grid, Html, Line, OrbitControls } from '@react-three/drei'
import { Canvas, useThree } from '@react-three/fiber'
import { forwardRef, useCallback, useImperativeHandle, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import { PATTERN_LABELS, type Target, type Telemetry, type Vec3 } from '../types/simulation'

export interface WorldOptions { grid: boolean; labels: boolean; trajectories: boolean; fov: boolean; beam: boolean; resetKey: number }
export interface WorldHandle { zoom: (factor: number) => void }

// Scene centre (the field-of-regard plane is centred on the origin, y = 5).
const CENTRE: Vec3 = [0, 5, 0]
const MIN_DISTANCE = 22
const MAX_DISTANCE = 340
const START_POSITION: Vec3 = [-20, 95, 118]
const labelStyle = { pointerEvents: 'none' as const, userSelect: 'none' as const }

function DroneBody({ color, emphasis = false }: { color: string; emphasis?: boolean }) {
  return <>
    <mesh><boxGeometry args={[4.8, 1.05, 2.8]} /><meshStandardMaterial color={color} metalness={.7} roughness={.28} emissive={emphasis ? '#0a2636' : '#071018'} emissiveIntensity={emphasis ? 1.1 : .25} /></mesh>
    {[-1, 1].map((x) => [-1, 1].map((z) => <group key={`${x}-${z}`} position={[x * 2.82, 0, z * 2.05]}>
      <mesh rotation={[0, 0, z * .32]}><cylinderGeometry args={[.10, .10, 3.25, 8]} /><meshStandardMaterial color="#91a9b7" metalness={.7} /></mesh>
      <mesh position={[0, .08, 0]} rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[1.04, 1.04, .08, 14]} /><meshStandardMaterial color="#263e4d" /></mesh>
    </group>))}
  </>
}

function TargetDrone({ target, labels, commState }: { target: Target; labels: boolean; commState: string }) {
  const [hovered, setHovered] = useState(false)
  if (target.role === 'decoy') {
    return <mesh position={target.position}><sphereGeometry args={[.7, 10, 10]} /><meshStandardMaterial color="#6d8494" emissive="#3d525e" emissiveIntensity={.5} /></mesh>
  }
  const glow = target.optical_visible ? (target.selected ? '#e8fdff' : '#a9d7e6') : '#4f6f80'
  return <group position={target.position} onPointerOver={(event) => { event.stopPropagation(); setHovered(true) }} onPointerOut={() => setHovered(false)}>
    <DroneBody color={target.selected ? '#258fb5' : '#4d6878'} emphasis={target.selected} />
    <mesh><sphereGeometry args={[target.selected ? .85 : .55, 18, 18]} /><meshStandardMaterial color={glow} emissive={glow} emissiveIntensity={target.selected ? 2.4 : .9} /></mesh>
    {target.selected && <mesh rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[1.6, .07, 8, 32]} /><meshBasicMaterial color={commState === 'CONNECTED' ? '#5be4b0' : '#e9b763'} /></mesh>}
    {(hovered || labels) && <Html position={[0, 4.4, 0]} center distanceFactor={20} style={labelStyle} zIndexRange={[20, 0]}>
      <div className={`target-tooltip ${target.selected ? 'primary' : ''}`}><strong>{target.label}{target.selected ? ' · selected' : ''}</strong><span>{PATTERN_LABELS[target.pattern]} path</span>{target.selected && <span>{commState}</span>}{target.hidden && <span>optical signal hidden</span>}</div>
    </Html>}
  </group>
}

function Terminal({ state }: { state: Telemetry }) {
  const [x, , z] = state.camera.aim_world
  const heading = Math.atan2(-z, x)
  return <group position={state.uav1.position}>
    <DroneBody color="#2b94c4" />
    <group rotation={[0, heading, 0]}>
      <mesh position={[1.4, -.76, 0]}><sphereGeometry args={[.64, 18, 18]} /><meshStandardMaterial color="#d9edf3" metalness={.75} roughness={.15} /></mesh>
    </group>
    <Html position={[0, 4.2, 0]} center distanceFactor={20} style={labelStyle} zIndexRange={[20, 0]}><span className="world-label">FSOC terminal · PTZ camera</span></Html>
  </group>
}

/** Actual camera footprint on the field of regard (exact FOV window) + boresight line. */
function Footprint({ state }: { state: Telemetry }) {
  const corners = state.camera.footprint
  const loop = useMemo(() => [...corners, corners[0]], [corners])
  return <>
    <Line points={loop} color="#69e5ef" lineWidth={1.6} transparent opacity={.95} />
    <mesh position={state.camera.aim_world} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[.9, 1.25, 24]} /><meshBasicMaterial color="#69e5ef" side={THREE.DoubleSide} /></mesh>
    <Line points={[state.uav1.position, state.camera.aim_world]} color="#69e5ef" lineWidth={1} transparent opacity={.55} dashed dashSize={2} gapSize={1.2} />
  </>
}

function KalmanGhost({ position }: { position: Vec3 }) {
  return <group position={position}>
    <mesh rotation={[Math.PI / 2, 0, 0]}><torusGeometry args={[1.8, .07, 8, 28]} /><meshBasicMaterial color="#65bbf2" transparent opacity={.95} /></mesh>
    <Html position={[0, 3, 0]} center distanceFactor={20} style={labelStyle}><span className="world-label predicted">Kalman prediction</span></Html>
  </group>
}

function ZoomBridge({ controls, onDistance, handleRef }: { controls: React.RefObject<OrbitControlsImpl>; onDistance: (d: number) => void; handleRef: React.Ref<WorldHandle> }) {
  const { camera } = useThree()
  useImperativeHandle(handleRef, () => ({
    zoom: (factor: number) => {
      const target = controls.current?.target ?? new THREE.Vector3(...CENTRE)
      const offset = camera.position.clone().sub(target)
      const distance = THREE.MathUtils.clamp(offset.length() * factor, MIN_DISTANCE, MAX_DISTANCE)
      camera.position.copy(target.clone().add(offset.setLength(distance)))
      controls.current?.update()
      onDistance(distance)
    },
  }), [camera, controls, onDistance])
  return null
}

function Scene({ state, options, handleRef, onDistance }: { state: Telemetry; options: WorldOptions; handleRef: React.Ref<WorldHandle>; onDistance: (d: number) => void }) {
  const controls = useRef<OrbitControlsImpl>(null)
  const target = useMemo(() => new THREE.Vector3(...CENTRE), [])
  const selected = state.targets.find((item) => item.selected)
  const beamColor = state.comm.state === 'CONNECTED' ? '#5be4b0' : state.comm.state === 'TRACKING' ? '#65bbf2' : state.system.los_clear ? '#e9b763' : '#ed6d76'
  const selectedTrail = state.trajectories[state.comm.target_id]
  return <>
    <color attach="background" args={['#06111d']} />
    {/* Fog far plane beyond the max zoom distance so zooming out never blanks the scene. */}
    <fog attach="fog" args={['#06111d', MAX_DISTANCE * .9, MAX_DISTANCE * 2.2]} />
    <ambientLight intensity={.55} /><directionalLight position={[84, 130, 30]} intensity={1.5} />
    {/* Layers are separated by >= 1 unit and the planes do not write depth, so
        the grid never z-fights with them at any zoom distance. */}
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -3, 0]} renderOrder={-2}><planeGeometry args={[900, 900]} /><meshBasicMaterial color="#051624" depthWrite={false} /></mesh>
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -2, 0]} renderOrder={-1}><planeGeometry args={[166.4, 105.6]} /><meshBasicMaterial color="#0b2b40" transparent opacity={.6} depthWrite={false} /></mesh>
    {options.grid && <Grid position={[0, -1, 0]} args={[600, 600]} cellSize={10} cellThickness={.55} cellColor="#1d4358" sectionSize={50} sectionThickness={1} sectionColor="#2f7a95" fadeDistance={MAX_DISTANCE * 1.6} fadeStrength={1.2} followCamera={false} infiniteGrid />}
    {state.environment.obstacle_enabled && <mesh position={state.environment.obstacle_center}><boxGeometry args={state.environment.obstacle_size} /><meshStandardMaterial color="#405968" transparent opacity={.87} /></mesh>}
    <Terminal state={state} />
    {state.targets.map((item) => <TargetDrone key={item.id} target={item} labels={options.labels && item.role === 'beacon'} commState={state.comm.state} />)}
    {options.beam && selected && <Line points={[state.uav1.position, selected.position]} color={beamColor} transparent opacity={.9} lineWidth={2.4} />}
    {options.fov && <Footprint state={state} />}
    {options.trajectories && Object.entries(state.trajectories).map(([id, points]) => points.length > 1 && id !== state.comm.target_id && <Line key={id} points={points} color="#45677a" transparent opacity={.35} lineWidth={.8} />)}
    {options.trajectories && selectedTrail && selectedTrail.length > 1 && <Line points={selectedTrail} color="#67dff2" transparent opacity={.85} lineWidth={1.8} />}
    {options.trajectories && state.camera_trajectory.length > 1 && <Line points={state.camera_trajectory} color="#e9b763" transparent opacity={.5} lineWidth={1} />}
    {state.tracking.prediction_active && state.tracking.predicted_world && <KalmanGhost position={state.tracking.predicted_world} />}
    <OrbitControls ref={controls} makeDefault target={target} enableDamping dampingFactor={.12} zoomSpeed={.9} minDistance={MIN_DISTANCE} maxDistance={MAX_DISTANCE} maxPolarAngle={Math.PI / 2.08} onChange={() => { const c = controls.current; if (c) onDistance(c.object.position.distanceTo(c.target)) }} />
    <ZoomBridge controls={controls} onDistance={onDistance} handleRef={handleRef} />
  </>
}

export const WorldScene = forwardRef<WorldHandle, { state: Telemetry; options: WorldOptions; onDistance: (d: number) => void }>(function WorldScene({ state, options, onDistance }, ref) {
  const wrapper = useRef<HTMLDivElement>(null)
  const report = useCallback((distance: number) => { wrapper.current?.setAttribute('data-camera-distance', distance.toFixed(2)); onDistance(distance) }, [onDistance])
  return <div className="world-canvas" ref={wrapper} data-camera-distance="">
    <Canvas key={options.resetKey} dpr={[1, 1.65]} camera={{ position: START_POSITION, fov: 46, near: 2, far: 2400 }}>
      <Scene state={state} options={options} handleRef={ref} onDistance={report} />
    </Canvas>
  </div>
})

export const ZOOM_LIMITS = { min: MIN_DISTANCE, max: MAX_DISTANCE, start: Math.hypot(START_POSITION[0] - CENTRE[0], START_POSITION[1] - CENTRE[1], START_POSITION[2] - CENTRE[2]) }
