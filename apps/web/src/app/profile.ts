"use client";

const PROFILE_STORAGE_KEY = "offerpilot.profile_id";
const PROFILE_HEADER = "X-OfferPilot-Profile-Id";

export function getProfileId(): string {
  const existing = window.localStorage.getItem(PROFILE_STORAGE_KEY);
  if (existing) return existing;
  const profileId = crypto.randomUUID();
  window.localStorage.setItem(PROFILE_STORAGE_KEY, profileId);
  return profileId;
}

export function withProfileHeaders(headers: Record<string, string> = {}): Record<string, string> {
  return { ...headers, [PROFILE_HEADER]: getProfileId() };
}
