import type { Member } from "../store/types";

interface Props {
    member: Member | null | undefined;
    size?: "sm" | "lg";
}

export function Avatar({ member, size = "sm" }: Props) {
    if (!member) {
        return (
            <span className={`avatar${size === "lg" ? " lg" : ""}`} title="Unassigned">
                ·
            </span>
        );
    }
    const initials = member.name
        .split(/\s+/)
        .map((p) => p[0])
        .slice(0, 2)
        .join("")
        .toUpperCase();
    return (
        <span
            className={`avatar${size === "lg" ? " lg" : ""}`}
            style={{ background: `${member.color}33`, color: member.color, borderColor: `${member.color}55` }}
            title={member.name}
        >
            {initials}
        </span>
    );
}
