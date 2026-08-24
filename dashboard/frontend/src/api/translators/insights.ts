import type {
  ApplicationInsights,
  DailySessionCount,
  InsightWindow,
} from '../../stats/model';
import type { components } from '../generated/schema';

type Schemas = components['schemas'];

function daily(wire: Schemas['DailySessionCountResponse']): DailySessionCount {
  return { date: wire.date, sessionCount: wire.session_count };
}

function window(wire: Schemas['InsightWindowResponse']): InsightWindow {
  return {
    sessionCount: wire.session_count,
    activeSessionCount: wire.active_session_count,
    finishedSessionCount: wire.finished_session_count,
    tokenCount: wire.token_count,
    costInUsd: wire.cost_in_usd,
    errorCount: wire.error_count,
    projects: wire.projects.map((project) => ({
      workingDirectory: project.working_directory,
      name: project.name,
      sessionCount: project.session_count,
    })),
  };
}

export function translateInsights(
  wire: Schemas['ApplicationInsightsResponse'],
): ApplicationInsights {
  return {
    generatedAt: wire.generated_at,
    totalSessionCount: wire.total_session_count,
    dailySessions: wire.daily_sessions.map(daily),
    hourlySessions: wire.hourly_sessions.map((row) => ({
      dayOfWeek: row.day_of_week,
      hour: row.hour,
      sessionCount: row.session_count,
    })),
    lastSevenDays: window(wire.last_seven_days),
    lastThirtyDays: window(wire.last_thirty_days),
    allTime: window(wire.all_time),
    projects: wire.projects.map((project) => ({
      workingDirectory: project.working_directory,
      name: project.name,
      sessionCount: project.session_count,
      tokenCount: project.token_count,
      costInUsd: project.cost_in_usd,
      errorCount: project.error_count,
      lastSessionAt: project.last_session_at,
      dailySessions: project.daily_sessions.map(daily),
    })),
  };
}
