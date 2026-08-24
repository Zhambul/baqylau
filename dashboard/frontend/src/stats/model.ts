export type DailySessionCount = {
  readonly date: string;
  readonly sessionCount: number;
};

type InsightProjectSummary = {
  readonly workingDirectory: string;
  readonly name: string;
  readonly sessionCount: number;
};

export type InsightWindow = {
  readonly sessionCount: number;
  readonly activeSessionCount: number;
  readonly finishedSessionCount: number;
  readonly tokenCount: number;
  readonly costInUsd: number;
  readonly errorCount: number;
  readonly projects: readonly InsightProjectSummary[];
};

export type ProjectInsights = {
  readonly workingDirectory: string;
  readonly name: string;
  readonly sessionCount: number;
  readonly tokenCount: number;
  readonly costInUsd: number;
  readonly errorCount: number;
  readonly lastSessionAt: number;
  readonly dailySessions: readonly DailySessionCount[];
};

export type ApplicationInsights = {
  readonly generatedAt: number;
  readonly totalSessionCount: number;
  readonly dailySessions: readonly DailySessionCount[];
  readonly hourlySessions: readonly {
    readonly dayOfWeek: number;
    readonly hour: number;
    readonly sessionCount: number;
  }[];
  readonly lastSevenDays: InsightWindow;
  readonly lastThirtyDays: InsightWindow;
  readonly allTime: InsightWindow;
  readonly projects: readonly ProjectInsights[];
};
