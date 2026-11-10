// Midpoint of an office day ("09:00" + "17:00" -> "13:00").
// Shared by the attendance settings summary (review finding m17) so the
// midday math lives in one place instead of being recomputed inline.
// NOTE: the backend also returns a computed `midday` with the saved
// settings — use that whenever the hours being shown are the saved ones;
// this helper is only for pending (unsaved) hours the backend has not
// seen yet.
export function middayLabel(startTime, endTime) {
  const toMin = (t) => {
    const [h, m] = t.split(":").map(Number);
    return h * 60 + m;
  };
  const mid = (toMin(startTime) + toMin(endTime)) / 2;
  return `${String(Math.floor(mid / 60)).padStart(2, "0")}:${String(mid % 60).padStart(2, "0")}`;
}
