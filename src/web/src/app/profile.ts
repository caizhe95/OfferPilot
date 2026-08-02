"use client";

const LEGACY_PROFILE_STORAGE_KEY = "offerpilot.profile_id";
let bootstrapPromise: Promise<void> | null = null;

export async function bootstrapProfile(): Promise<void> {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = (async () => {
    const legacyProfileId = window.localStorage.getItem(LEGACY_PROFILE_STORAGE_KEY);
    const response = await fetch("/api/profile/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(legacyProfileId ? { legacy_profile_id: legacyProfileId } : {}),
    });
    if (!response.ok) throw new Error("Profile initialization failed");
    if (legacyProfileId) window.localStorage.removeItem(LEGACY_PROFILE_STORAGE_KEY);
  })();
  try {
    await bootstrapPromise;
  } catch (error) {
    bootstrapPromise = null;
    throw error;
  }
}

export function withProfileHeaders(headers: Record<string, string> = {}): Record<string, string> {
  return headers;
}
