const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type ActionState = {
  success: boolean;
  message: string;
} | null;

export async function submitContactForm(data: {
  name: string;
  email: string;
  company: string;
  subject: string;
  message: string;
  _honey: string;
}): Promise<ActionState> {
  try {
    const resp = await fetch(`${BASE}/api/contact`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });

    const result = await resp.json();

    if (!resp.ok) {
      // Pydantic validation errors
      if (result.detail && Array.isArray(result.detail)) {
        return { success: false, message: "Please check your input and try again." };
      }
      return {
        success: false,
        message: result.message || "Something went wrong. Please try again or email us directly at sid@faradworks.com.",
      };
    }

    return result as ActionState;
  } catch {
    return {
      success: false,
      message: "Could not reach the server. Please try again or email us directly at sid@faradworks.com.",
    };
  }
}
