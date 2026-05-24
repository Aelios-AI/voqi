import { useStore } from "../store";

export function Toasts() {
    const toasts = useStore((s) => s.toasts);
    return (
        <div className="toast-stack">
            {toasts.map((t) => (
                <div key={t.id} className={`toast${t.kind ? ` ${t.kind}` : ""}`}>
                    <span className="toast-dot" />
                    <span>{t.message}</span>
                </div>
            ))}
        </div>
    );
}
