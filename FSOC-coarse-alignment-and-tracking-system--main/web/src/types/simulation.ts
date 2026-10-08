export type Vec3 = [number, number, number]
export type Vec2 = [number, number]

export interface Disturbances {
  noise: { gaussian: number; salt_pepper: number; poisson: number }
  jitter: { amplitude_px: number }
  platform: { pattern: string; amplitude_px: number; period_s: number }
  atmosphere: { condition: string; intensity: number }
  turbulence: { strength: number }
}

export interface BeaconConfig { id: string; pattern: string; speed: number; center_pct: Vec2; size: number }

export interface AppConfig {
  camera: { resolution: Vec2; fov_h_deg: number; fov_v_deg: number; update_rate_hz: number; initial_position: { mode: 'center' | 'custom'; x_pct: number; y_pct: number }; max_pan_dps: number; max_tilt_dps: number }
  targets: { count: number; size_px: number; decoy_count: number; beacons: BeaconConfig[] }
  tracking: { detector: 'classical' | 'yolo'; acquisition: 'cue' | 'search'; cue_error_deg: number; confirm_frames: number; coast_frames: number; link_threshold_px: number }
  comm: { target_id: string; mode: 'auto' | 'manual' }
  simulation: { speed_multiplier: number; beacon_hidden: boolean }
  environment: { obstacle_enabled: boolean; obstacle_center: Vec3; obstacle_size: Vec3 }
  disturbances: Disturbances
  video: { apply_disturbances: boolean }
}

export interface Target {
  id: string
  label: string
  role: 'beacon' | 'decoy'
  selected: boolean
  pattern: string
  position: Vec3
  optical_visible: boolean
  hidden: boolean
  angles: Vec2
  confidence: number
}

export interface SihItem { key: string; label: string; value: number | null; limit: number; op: '<=' | '<' | '>='; unit: string; pass: boolean | null }

export interface Metrics {
  frames: number
  duration_s: number
  loop_fps: number
  processing_ms_mean: number
  processing_ms_max: number
  processing_fps: number
  acquisition_time_s: number | null
  acquisition_times_s: number[]
  reacquisition_time_mean_s: number | null
  reacquisition_time_max_s: number | null
  reacquisition_count: number
  error_mean_px: number | null
  error_max_px: number | null
  error_rmse_px: number | null
  target_loss_pct: number | null
  lock_retention_pct: number | null
  hidden_time_s: number
  sih: SihItem[]
}

export interface PerformanceSummary extends Partial<Metrics> {
  session_id: string
  started_at?: string
  source?: string
  duration?: number
  average_fps?: number
  average_tracking_error?: number | null
  fsoc_link_retention_rate?: number
  fov_compliance?: number
  total_frames_processed?: number
  scenario?: string
  selected_beacon?: string
  [key: string]: unknown
}

export interface HistoryPoint {
  timestamp: number
  target_azimuth: number
  target_elevation: number
  camera_pan: number
  camera_tilt: number
  pixel_error: number | null
  angular_error: number
  pan_error: number
  tilt_error: number
  confidence: number
  fps: number
  processing_time_ms: number
  disturbance_level: number
  fsoc_link: boolean
  beacon_hidden: boolean
  locked: boolean
  detected: boolean
  pan_rate: number
  tilt_rate: number
}

export interface ScenarioState {
  id: string
  name: string
  source: 'default' | 'preset' | 'saved'
  loaded_at: string | null
  modified: boolean
  description: string
  status: 'LOADED' | 'ACTIVE'
  applies_to: { simulation: boolean; video: boolean }
}

export type CommState = 'IDLE' | 'ACQUIRING' | 'TRACKING' | 'CONNECTED'

export interface VideoRow {
  frame: number; time_s: number; detected: boolean; locked: boolean; state: string
  centroid_x: number | null; centroid_y: number | null; camera_centre_x: number; camera_centre_y: number
  tracking_error_px: number | null; offset_from_frame_centre_px: number | null
  pan_cmd_deg: number; tilt_cmd_deg: number; confidence: number; candidates: number; processing_ms: number
}

export interface VideoState {
  status: 'EMPTY' | 'READY' | 'PROCESSING' | 'COMPLETE' | 'STOPPED' | 'ERROR'
  message: string
  meta: { filename?: string; width?: number; height?: number; fps?: number; fps_reported?: number | null; frames?: number; duration_s?: number | null; is_30fps?: boolean; detector_used?: string; frames_processed?: number }
  run_id: string | null
  frame_index: number
  progress: number | null
  image: string
  current: Partial<VideoRow>
  summary: Metrics & { frames_processed: number }
  series: { t: number; error: number | null; detected: boolean; locked: boolean }[]
  events: { timestamp: number; message: string; category: string }[]
  settings: Record<string, unknown>
  report: { run_id: string; csv: string; pdf: string; json: string } | null
}

export interface Telemetry {
  type: 'telemetry'
  timestamp: number
  running: boolean
  comm: { active: boolean; state: CommState; target_id: string; target_label: string; mode: 'auto' | 'manual'; path: string | null; link_threshold_px: number }
  active_target_id: string
  uav1: { id: string; position: Vec3 }
  targets: Target[]
  camera: {
    pan: number; tilt: number; commanded_pan: number; commanded_tilt: number; pan_rate: number; tilt_rate: number; rate_limited: boolean
    fov_horizontal: number; fov_vertical: number; resolution: Vec2; update_rate_hz: number; max_pan_dps: number; max_tilt_dps: number
    aim_world: Vec3; footprint: Vec3[]; los_offset_px: Vec2
  }
  field: { width_deg: number; height_deg: number }
  target_angles: { azimuth: number; elevation: number; distance: number }
  tracking: {
    state: 'SEARCHING' | 'ACQUIRING' | 'LOCKED' | 'COASTING'
    pixel_error: number | null; detected: boolean; locked: boolean; confidence: number; detector: string
    measured: Vec2 | null; predicted: Vec2 | null; truth: Vec2 | null; predicted_world: Vec3 | null
    prediction_active: boolean; candidates: number; angular_error: number
  }
  system: { fps: number; processing_time_ms: number; fov_ok: boolean; los_clear: boolean; fsoc_link: boolean; link_reason: string }
  disturbances: Disturbances
  disturbance_labels: string[]
  environment: { obstacle_enabled: boolean; obstacle_center: Vec3; obstacle_size: Vec3 }
  simulation: { speed_multiplier: number; beacon_hidden: boolean; hidden_id: string | null; beacon_count: number; decoy_count: number }
  scenario: ScenarioState
  camera_view: { width: number; height: number; image: string }
  trajectories: Record<string, Vec3[]>
  camera_trajectory: Vec3[]
  history: HistoryPoint[]
  events: { timestamp: number; message: string; category: string }[]
  performance: PerformanceSummary
  sih_limits: Record<string, number>
  config: AppConfig
  video?: VideoState
}

export type DeepPartial<T> = { [K in keyof T]?: T[K] extends (infer U)[] ? DeepPartial<U>[] | T[K] : T[K] extends object ? DeepPartial<T[K]> : T[K] }
export type Command =
  | { action: 'start' | 'pause' | 'reset' | 'reset_defaults' | 'toggle_beacon' | 'generate_report' | 'stop_comm' }
  | { action: 'configure'; config: DeepPartial<AppConfig> }
  | { action: 'set_target'; target_id: string }
  | { action: 'start_comm'; target_id?: string }
  | { action: 'set_lock_mode'; lock_mode: 'auto' | 'manual' }
  | { action: 'load_preset'; preset_id: string }

export const PATTERN_LABELS: Record<string, string> = {
  straight: 'Straight line', circular: 'Circular', figure8: 'Figure-8', random: 'Random', spiral: 'Spiral', sinusoidal: 'Sinusoidal', drift: 'Drift',
}

export const fmt = (value: number | null | undefined, digits = 2, unit = '') => value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(digits)}${unit}`
