export const sleep = (ms: number): Promise<void> =>
    new Promise((resolve) => setTimeout(resolve, ms));

export const fakeLatency = (lo = 120, hi = 380): Promise<void> =>
    sleep(lo + Math.random() * (hi - lo));
