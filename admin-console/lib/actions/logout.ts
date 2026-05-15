"use client";

import { signOut } from "next-auth/react";

export async function logoutAction() {
  try {
    await signOut({ redirect: true, callbackUrl: "/login" });
  } catch (err) {
    console.error(err);
    throw new Error("No se pudo cerrar sesión");
  }
}
