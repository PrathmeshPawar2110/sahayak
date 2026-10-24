import { http, HttpResponse } from "msw";

const API_BASE = "http://localhost:8000";
export const SESSION_ID = "11111111-1111-1111-1111-111111111111";

export const handlers = [
  http.post(`${API_BASE}/v1/sessions`, async ({ request }) => {
    const body = (await request.json()) as { language?: string | null };
    return HttpResponse.json(
      {
        session_id: SESSION_ID,
        channel: "web",
        language: body.language ?? null,
        expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        consents: {},
      },
      { status: 201 },
    );
  }),

  http.post(`${API_BASE}/v1/sessions/${SESSION_ID}/consent`, () =>
    HttpResponse.json({
      consents: { profile: { granted_at: new Date().toISOString(), policy_version: "test" } },
    }),
  ),

  http.post(`${API_BASE}/v1/sessions/${SESSION_ID}/messages`, async ({ request }) => {
    const body = (await request.json()) as { text: string };
    return HttpResponse.json({
      reply_text: `echo: ${body.text}`,
      reply_audio_url: null,
      kind: "answer",
      language: "en",
      intent: "ask_eligibility",
      question_field: null,
      eligibility: {
        ready: [
          {
            scheme_id: "pm-kisan",
            name: "PM-KISAN",
            provisional: false,
            reasons: ["Owns farmland"],
            official_url: "https://pmkisan.gov.in",
            last_verified_at: "2026-01-01T00:00:00Z",
          },
        ],
        action_needed: [],
        provisional: [],
      },
      citations: [
        {
          number: 1,
          chunk_id: "chunk-1",
          scheme_id: "pm-kisan",
          scheme_name: "PM-KISAN",
          section: "Eligibility",
          source_url: "https://pmkisan.gov.in/eligibility",
          last_verified_at: "2026-01-01T00:00:00Z",
        },
      ],
      session_deleted: false,
    });
  }),

  http.get(`${API_BASE}/v1/sessions/${SESSION_ID}/profile`, () =>
    HttpResponse.json({
      session_id: SESSION_ID,
      language: "en",
      profile: { age: 45, occupation: "farmer" },
      field_sources: {},
    }),
  ),

  http.get(`${API_BASE}/v1/schemes/pm-kisan`, () =>
    HttpResponse.json({
      id: "pm-kisan",
      name: "PM-KISAN",
      names: { en: "PM-KISAN", hi: "PM-KISAN", mr: "PM-KISAN" },
      level: "central",
      state: null,
      status: "active",
      benefits: "₹6,000 per year in three instalments",
      official_url: "https://pmkisan.gov.in",
      last_verified_at: "2026-01-01T00:00:00Z",
      aliases: [],
      ministry: "Agriculture",
      documents_required: [],
      application_url: "https://pmkisan.gov.in",
      source_url: "https://pmkisan.gov.in",
    }),
  ),

  http.delete(`${API_BASE}/v1/sessions/${SESSION_ID}`, () => new HttpResponse(null, { status: 204 })),
];
