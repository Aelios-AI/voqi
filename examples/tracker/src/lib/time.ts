const DAY = 24 * 60 * 60 * 1000;

export function formatRelative(ts: number): string {
    const diff = ts - Date.now();
    const abs = Math.abs(diff);
    const future = diff > 0;
    if (abs < 60 * 1000) return future ? "in a moment" : "just now";
    if (abs < 60 * 60 * 1000) {
        const m = Math.round(abs / (60 * 1000));
        return future ? `in ${m}m` : `${m}m ago`;
    }
    if (abs < DAY) {
        const h = Math.round(abs / (60 * 60 * 1000));
        return future ? `in ${h}h` : `${h}h ago`;
    }
    const d = Math.round(abs / DAY);
    if (d <= 14) return future ? `in ${d}d` : `${d}d ago`;
    return new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatDate(ts: number | null): string {
    if (ts == null) return "—";
    return new Date(ts).toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: ts < Date.now() - 180 * DAY || ts > Date.now() + 180 * DAY ? "numeric" : undefined,
    });
}

export function isOverdue(ts: number | null): boolean {
    return ts != null && ts < Date.now();
}

export function parseHumanDate(input: string): number | null {
    const trimmed = input.trim().toLowerCase();
    if (!trimmed) return null;
    const now = new Date();
    if (trimmed === "today") return now.setHours(17, 0, 0, 0);
    if (trimmed === "tomorrow") {
        const d = new Date();
        d.setDate(d.getDate() + 1);
        return d.setHours(17, 0, 0, 0);
    }
    const days: Record<string, number> = {
        sunday: 0, monday: 1, tuesday: 2, wednesday: 3,
        thursday: 4, friday: 5, saturday: 6,
    };
    if (days[trimmed] !== undefined) {
        const d = new Date();
        const target = days[trimmed];
        const diff = (target + 7 - d.getDay()) % 7 || 7;
        d.setDate(d.getDate() + diff);
        return d.setHours(17, 0, 0, 0);
    }
    const m = trimmed.match(/^in (\d+) (day|days|week|weeks)$/);
    if (m) {
        const n = parseInt(m[1], 10) * (m[2].startsWith("week") ? 7 : 1);
        return Date.now() + n * DAY;
    }
    const parsed = Date.parse(input);
    return Number.isNaN(parsed) ? null : parsed;
}
