import client from "./client";

export function login(email, password) {
  return client.post("/api/auth/login", { email, password }).then((r) => r.data);
}

export function me() {
  return client.get("/api/auth/me").then((r) => r.data);
}
