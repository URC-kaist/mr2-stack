import type { DiagnosticArray } from './rosMessages'

export type ChronyRole = 'base' | 'rover'

export type ChronyStatus = {
  schemaVersion: 1
  role: ChronyRole
  available: boolean
  synchronized: boolean
  referenceId: string | null
  referenceName: string | null
  stratum: number | null
  systemTimeOffsetS: number | null
  lastOffsetS: number | null
  rmsOffsetS: number | null
  rootDelayS: number | null
  rootDispersionS: number | null
  updateIntervalS: number | null
  leapStatus: string | null
  sampledAtEpochMs: number
  receivedAtEpochMs: number
  error: string | null
  policyAvailable: boolean
  policyLocked: boolean
  baseLocked: boolean
  relativeReady: boolean
  basePeer: string | null
  selectedSource: string | null
  utcState: string
  clockState?: string
  clockErrorBoundS?: number | null
  clockEpoch?: string | null
  baseClockEpoch?: string | null
  clockDriftBoundPpm?: number | null
}

export type ChronyReadiness = {
  ready: boolean
  reasons: string[]
  roverErrorBoundS: number | null
}

export const CHRONY_MAX_AGE_MS = 5_000
export const CHRONY_MAX_OFFSET_S = 0.002
export const CHRONY_MAX_ERROR_BOUND_S = 0.002

const finiteOrNull = (value: unknown) => {
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

const stringOrNull = (value: unknown) =>
  typeof value === 'string' && value.length > 0 ? value : null

const booleanValue = (value: unknown, field: string) => {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    if (value.toLowerCase() === 'true') return true
    if (value.toLowerCase() === 'false') return false
  }
  throw new Error(`${field} must be a boolean`)
}

function parseObject(
  value: Record<string, unknown>,
  expectedRole: ChronyRole,
  receivedAtEpochMs: number
): ChronyStatus {
  const role = value.role
  if (Number(value.schema_version) !== 1) throw new Error('chrony schema_version must be 1')
  if (role !== expectedRole) throw new Error(`chrony role must be ${expectedRole}`)
  const sampledAtEpochMs = finiteOrNull(value.sampled_at_epoch_ms)
  if (sampledAtEpochMs == null) throw new Error('chrony sampled_at_epoch_ms is required')

  return {
    schemaVersion: 1,
    role: expectedRole,
    available: booleanValue(value.available, 'available'),
    synchronized: booleanValue(value.synchronized, 'synchronized'),
    referenceId: stringOrNull(value.reference_id),
    referenceName: stringOrNull(value.reference_name),
    stratum: finiteOrNull(value.stratum),
    systemTimeOffsetS: finiteOrNull(value.system_time_offset_s),
    lastOffsetS: finiteOrNull(value.last_offset_s),
    rmsOffsetS: finiteOrNull(value.rms_offset_s),
    rootDelayS: finiteOrNull(value.root_delay_s),
    rootDispersionS: finiteOrNull(value.root_dispersion_s),
    updateIntervalS: finiteOrNull(value.update_interval_s),
    leapStatus: stringOrNull(value.leap_status),
    sampledAtEpochMs,
    receivedAtEpochMs,
    error: stringOrNull(value.error),
    policyAvailable: booleanValue(value.policy_available ?? false, 'policy_available'),
    policyLocked: booleanValue(value.policy_locked ?? false, 'policy_locked'),
    baseLocked: booleanValue(value.base_locked ?? false, 'base_locked'),
    relativeReady: booleanValue(value.relative_ready ?? false, 'relative_ready'),
    basePeer: stringOrNull(value.base_peer),
    selectedSource: stringOrNull(value.selected_source),
    utcState: stringOrNull(value.utc_state) ?? 'unverified',
    clockState: stringOrNull(value.clock_state) ?? 'invalid',
    clockErrorBoundS: finiteOrNull(value.clock_error_bound_s),
    clockEpoch: stringOrNull(value.clock_epoch),
    baseClockEpoch: stringOrNull(value.base_clock_epoch),
    clockDriftBoundPpm: finiteOrNull(value.clock_drift_bound_ppm),
  }
}

function resolveClockStatusUrl() {
  const explicit = import.meta.env.VITE_XBEE_WS_URL as string | undefined
  if (explicit) {
    const url = new URL(explicit, window.location.href)
    url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:'
    url.pathname = '/latency/clock-status'
    url.search = ''
    url.hash = ''
    return url.toString()
  }
  return `${window.location.origin}/latency/clock-status`
}

export async function fetchBaseChronyStatus(): Promise<ChronyStatus> {
  const response = await fetch(resolveClockStatusUrl(), { cache: 'no-store' })
  if (!response.ok) throw new Error(`Clock status endpoint returned ${response.status}`)
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Clock status endpoint returned a non-JSON response')
  }
  const value = (await response.json()) as unknown
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('Clock status endpoint returned invalid JSON')
  }
  return parseObject(value as Record<string, unknown>, 'base', Date.now())
}

export function parseRoverChronyDiagnostic(
  message: DiagnosticArray,
  receivedAtEpochMs = Date.now()
): ChronyStatus {
  const status = message.status?.[0]
  if (!status) throw new Error('Rover clock diagnostic has no status')
  const values: Record<string, unknown> = {}
  for (const entry of status.values || []) values[entry.key] = entry.value
  values.error = values.error || (status.level > 0 ? status.message : '')
  return parseObject(values, 'rover', receivedAtEpochMs)
}

export function evaluateChronyReadiness(
  base: ChronyStatus | null,
  rover: ChronyStatus | null,
  nowEpochMs = Date.now()
): ChronyReadiness {
  const reasons: string[] = []
  const checkHost = (label: string, status: ChronyStatus | null) => {
    if (!status) {
      reasons.push(`${label} chrony status is missing`)
      return
    }
    const receiveAgeMs = nowEpochMs - status.receivedAtEpochMs
    // Offline base UTC may differ from the browser's wall clock. Policy age is
    // checked on each host's monotonic clock; use receipt age in this clock domain.
    if (Math.abs(receiveAgeMs) > CHRONY_MAX_AGE_MS) {
      reasons.push(`${label} chrony status is stale`)
    }
    if (!status.available) reasons.push(`${label} chronyc is unavailable`)
    if (!status.synchronized) reasons.push(`${label} clock is not synchronized`)
    if (!status.policyAvailable) reasons.push(`${label} clock policy is unavailable or stale`)
    if (!status.policyLocked) reasons.push(`${label} clock is not locked for this launch`)
  }
  checkHost('Base', base)
  checkHost('Rover', rover)
  if (rover && (!rover.baseLocked || !rover.basePeer || rover.selectedSource !== rover.basePeer)) {
    reasons.push('Rover is not synchronized to the locked base')
  }
  if (rover && !rover.relativeReady) reasons.push('Latest base time sample is stale or exceeds 2 ms')
  if (base?.clockEpoch && rover?.baseClockEpoch !== base.clockEpoch) {
    reasons.push('Base clock epoch changed')
  }

  const roverOffsetS = rover?.systemTimeOffsetS
  if (rover && roverOffsetS == null) {
    reasons.push('Rover system offset is unavailable')
  } else if (roverOffsetS != null && Math.abs(roverOffsetS) > CHRONY_MAX_OFFSET_S) {
    reasons.push('Rover system offset exceeds 2 ms')
  }

  const roverErrorBoundS =
    rover?.rootDispersionS != null && rover.rootDelayS != null
      ? Math.abs(rover.systemTimeOffsetS ?? Infinity) + rover.rootDispersionS + 0.5 * Math.abs(rover.rootDelayS)
      : null
  const reportedBound = rover?.clockErrorBoundS
  if (reportedBound != null && (!Number.isFinite(reportedBound) || reportedBound < 0)) {
    reasons.push('Clock uncertainty is unavailable')
  }
  let combinedBound = rover != null && roverErrorBoundS != null && reportedBound != null ?
    Math.max(roverErrorBoundS, reportedBound + Math.max(0, nowEpochMs - rover.receivedAtEpochMs) *
      Math.max(10, rover.clockDriftBoundPpm ?? 10) * 1e-9) : roverErrorBoundS
  if (combinedBound != null) combinedBound += Math.abs(base?.systemTimeOffsetS ?? Infinity)
  if (rover && roverErrorBoundS == null) {
    reasons.push('Rover clock error bound is unavailable')
  } else if (combinedBound != null && combinedBound > CHRONY_MAX_ERROR_BOUND_S) {
    reasons.push('Rover clock error bound exceeds 2 ms')
  }

  return { ready: reasons.length === 0, reasons, roverErrorBoundS: combinedBound }
}

export function chronyStatusForLog(status: ChronyStatus | null) {
  if (!status) return null
  return {
    role: status.role,
    available: status.available,
    synchronized: status.synchronized,
    reference_id: status.referenceId,
    reference_name: status.referenceName,
    stratum: status.stratum,
    system_time_offset_s: status.systemTimeOffsetS,
    root_delay_s: status.rootDelayS,
    root_dispersion_s: status.rootDispersionS,
    sampled_at_epoch_ms: status.sampledAtEpochMs,
    received_at_epoch_ms: status.receivedAtEpochMs,
    error: status.error,
    policy_available: status.policyAvailable,
    policy_locked: status.policyLocked,
    base_locked: status.baseLocked,
    relative_ready: status.relativeReady,
    base_peer: status.basePeer,
    selected_source: status.selectedSource,
    utc_state: status.utcState,
  }
}
