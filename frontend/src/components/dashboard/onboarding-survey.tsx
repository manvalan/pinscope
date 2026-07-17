"use client";

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fetchSurveyStatus, submitSurvey } from "@/lib/api";

const REFERRAL_OPTIONS = [
  { value: "google", label: "Google search" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "twitter", label: "Twitter / X" },
  { value: "word_of_mouth", label: "Word of mouth" },
  { value: "conference", label: "Conference / event" },
  { value: "other", label: "Other" },
];

const PROFILE_OPTIONS = [
  { value: "hobbyist", label: "Hobbyist / maker" },
  { value: "professional", label: "Professional engineer" },
  { value: "student", label: "Student" },
  { value: "manager", label: "Engineering manager" },
];

export function OnboardingSurvey() {
  const [open, setOpen] = useState(false);
  const [referral, setReferral] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    fetchSurveyStatus().then((s) => {
      if (!s.completed) setOpen(true);
    });
  }, []);

  async function handleSubmit() {
    if (!referral || !profile) return;
    setSubmitting(true);
    await submitSurvey({
      referral_source: referral,
      user_profile: profile,
    });
    setSubmitting(false);
    setOpen(false);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent showCloseButton={false} className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Welcome to Pinscope</DialogTitle>
          <DialogDescription>
            Two quick questions to help us improve the product.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="referral">How did you hear about us?</Label>
            <Select
              value={referral}
              onValueChange={(val) => setReferral(val)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select one..." />
              </SelectTrigger>
              <SelectContent>
                {REFERRAL_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="profile">What best describes you?</Label>
            <Select
              value={profile}
              onValueChange={(val) => setProfile(val)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select one..." />
              </SelectTrigger>
              <SelectContent>
                {PROFILE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOpen(false)}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={!referral || !profile || submitting}
            onClick={handleSubmit}
          >
            {submitting ? "Saving..." : "Continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
