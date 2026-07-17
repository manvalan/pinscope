"use client";

import { useState } from "react";
import { Loader2, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { submitContactForm, type ActionState } from "./actions";

export function ContactForm() {
  const [state, setState] = useState<ActionState>(null);
  const [pending, setPending] = useState(false);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);

    const form = e.currentTarget;
    const formData = new FormData(form);

    const result = await submitContactForm({
      name: (formData.get("name") as string) ?? "",
      email: (formData.get("email") as string) ?? "",
      company: (formData.get("company") as string) ?? "",
      subject: (formData.get("subject") as string) ?? "",
      message: (formData.get("message") as string) ?? "",
      _honey: (formData.get("_honey") as string) ?? "",
    });

    setState(result);
    setPending(false);
  }

  if (state?.success) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <CheckCircle2 className="h-12 w-12 text-emerald-500 mb-4" />
        <h3 className="font-headline text-xl tracking-tight">Message sent</h3>
        <p className="mt-2 text-sm text-muted-foreground max-w-sm">
          {state.message}
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Honeypot */}
      <input
        name="_honey"
        type="text"
        className="sr-only"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            name="name"
            required
            maxLength={200}
            placeholder="Your name"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            name="email"
            type="email"
            required
            maxLength={254}
            placeholder="you@company.com"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="company">
            Company <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Input
            id="company"
            name="company"
            maxLength={200}
            placeholder="Company name"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="subject">
            Subject <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Input
            id="subject"
            name="subject"
            maxLength={200}
            placeholder="What's this about?"
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="message">Message</Label>
        <Textarea
          id="message"
          name="message"
          required
          maxLength={5000}
          rows={6}
          placeholder="How can we help?"
        />
      </div>

      {state?.success === false && (
        <p className="text-sm text-destructive" role="alert">
          {state.message}
        </p>
      )}

      <Button
        type="submit"
        disabled={pending}
        className="bg-blue-600 hover:bg-blue-500 text-white border-0 px-8 text-base h-12 w-full sm:w-auto"
      >
        {pending ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Sending...
          </>
        ) : (
          "Send message"
        )}
      </Button>
    </form>
  );
}
