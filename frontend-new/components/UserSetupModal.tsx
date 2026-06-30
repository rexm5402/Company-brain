"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { setCurrentUser } from "@/lib/useCurrentUser";

const KEY = "brain_os_user";

export function UserSetupModal() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");

  useEffect(() => {
    if (!localStorage.getItem(KEY)) setOpen(true);
  }, []);

  const save = () => {
    if (!value.trim()) return;
    setCurrentUser(value.trim());
    setOpen(false);
    // reload so hooks re-read localStorage
    window.location.reload();
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="bg-card border border-border rounded-2xl p-6 w-80 shadow-xl space-y-4">
        <div>
          <h2 className="text-base font-semibold">Welcome to Brain OS</h2>
          <p className="text-sm text-muted-foreground mt-1">Enter your GitHub username to continue.</p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="gh-user">GitHub username</Label>
          <Input
            id="gh-user"
            placeholder="e.g. aryanmurugesh"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
            autoFocus
          />
        </div>
        <Button className="w-full" onClick={save} disabled={!value.trim()}>
          Continue
        </Button>
      </div>
    </div>
  );
}
