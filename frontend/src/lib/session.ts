const OPEN_MINUTE = 9 * 60 + 30;
const LUNCH_START = 11 * 60 + 30;
const LUNCH_END = 13 * 60;
const CLOSE_MINUTE = 15 * 60;

export function clockToMinute(clock: string): number | null {
  const match = clock.trim().match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

export function minuteToClock(minute: number): string {
  const hour = Math.floor(minute / 60);
  const mins = minute % 60;
  return `${String(hour).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

export function aShareSessionClocks(stepMinutes = 1): string[] {
  const step = Math.max(1, stepMinutes);
  const clocks: string[] = [];
  let minute = OPEN_MINUTE;
  while (minute <= CLOSE_MINUTE) {
    if (minute > LUNCH_START && minute < LUNCH_END) {
      minute = LUNCH_END;
      continue;
    }
    clocks.push(minuteToClock(minute));
    minute += step;
  }
  return clocks;
}

export function detectIntradayStep(times: string[]): number {
  const minutes = times.map(clockToMinute).filter((item): item is number => item != null);
  for (let index = 1; index < minutes.length; index += 1) {
    const gap = minutes[index] - minutes[index - 1];
    if (gap <= 0 || gap >= 60) continue;
    if (minutes[index - 1] === LUNCH_START && minutes[index] === LUNCH_END) continue;
    return gap;
  }
  return 1;
}

export function extendIntradayTimes(times: string[]): string[] {
  const step = detectIntradayStep(times);
  const session = aShareSessionClocks(step);
  if (times.length === 0) return session;
  const lastMinute = clockToMinute(times[times.length - 1]);
  if (lastMinute == null) return [...times, ...session];
  const extra = session.filter((clock) => {
    const minute = clockToMinute(clock);
    return minute != null && minute > lastMinute;
  });
  return [...times, ...extra];
}
