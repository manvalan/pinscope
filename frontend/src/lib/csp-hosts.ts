// Open-core seam: third-party hosts allow-listed in the CSP
// (next.config.ts). The cloud/gateway build adds Clerk (auth), Stripe
// (billing), and its analytics hosts here; the open-source build needs
// none.

export const CSP_SCRIPT_HOSTS: string[] = [];

export const CSP_CONNECT_HOSTS: string[] = [];

export const CSP_FRAME_HOSTS: string[] = [];
