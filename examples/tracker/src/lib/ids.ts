export const newId = (prefix: string): string =>
    `${prefix}_${Math.random().toString(36).slice(2, 10)}`;

let _seq = 1000;
export const newTaskCode = (): string => `EX-${++_seq}`;
