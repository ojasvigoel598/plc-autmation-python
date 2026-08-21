import React, { useMemo, useRef } from "react";
import { useStore } from "zustand";
import { OrbitControls } from "@react-three/drei";
import {
  createXRStore,
  useXR,
  useXRControllerLocomotion,
  useXRSessionModeSupported,
  XR,
  TeleportTarget,
} from "@react-three/xr";

/*
 * WebXR / VR integration.
 *
 * The 3D plant is the SAME scene in desktop and VR: both run from the same
 * `state`/`config` props, so the VR view is never a separate copy of the
 * simulation.  This module only adds:
 *
 *   - a store + <XR> wrapper (headset session, controllers, ray pointers),
 *   - a VR entry button (hidden when the browser has no WebXR),
 *   - thumbstick locomotion (left stick move, right stick snap-turn),
 *   - a teleport pad on the plant floor for safer long-distance travel.
 *
 * The default controller ray pointer routes through @react-three/xr's
 * pointer-events system, which reuses the SAME `onClick` handlers the mouse
 * uses — so selecting a tank/valve/pump works identically in VR.
 */

/* Single store shared by the button and the scene (module-level singleton). */
export const xrStore = createXRStore();

/* ------------------------------------------------------------------ */
/* VR entry button (rendered in the DOM, outside the Canvas)          */
/* ------------------------------------------------------------------ */
export function VRButton() {
  const session = useStore(xrStore, (s) => s.session);
  const supported = useXRSessionModeSupported("immersive-vr");
  if (!supported) return null;

  const inVR = session != null;
  return (
    <button
      className={`vr-btn ${inVR ? "on" : ""}`}
      onClick={() => (inVR ? session.end() : xrStore.enterXR("immersive-vr"))}
    >
      {inVR ? "◉ EXIT VR" : "🥽 ENTER VR"}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/* Scene wrapper: activates the headset session + controllers         */
/* ------------------------------------------------------------------ */
export function XRScene({ children }) {
  return <XR store={xrStore}>{children}</XR>;
}

/* ------------------------------------------------------------------ */
/* Locomotion: left stick = walk, right stick = snap-turn             */
/* ------------------------------------------------------------------ */
function LocomotionRig({ children }) {
  const rig = useRef(null);
  // left-thumbstick smooth translation; right-thumbstick 30° snap turn
  useXRControllerLocomotion(
    rig,
    { speed: 2.2 },
    { type: "snap", degrees: 30 },
    "left"
  );
  return <group ref={rig}>{children}</group>;
}

/* Teleport destination on the plant floor (safe long-distance travel). */
function TeleportPad() {
  return (
    <TeleportTarget>
      {/* The pad is the same concrete floor the plant sits on; clicking it
          with the teleport pointer moves the rig to that point. */}
      <mesh
        rotation={[-Math.PI / 2, 0, 0]}
        position={[3, -0.03, 0]}
        visible={false}
      >
        <planeGeometry args={[30, 8]} />
      </mesh>
    </TeleportTarget>
  );
}

/* ------------------------------------------------------------------ */
/* Compose everything: XR + rig locomotion + teleport floor           */
/* ------------------------------------------------------------------ */
export function WebXRScene({ children }) {
  const content = useMemo(() => <LocomotionRig>{children}</LocomotionRig>, [children]);
  return (
    <XRScene>
      <TeleportPad />
      {content}
    </XRScene>
  );
}

/* ------------------------------------------------------------------ */
/* VR-aware orbit controls                                            */
/* ------------------------------------------------------------------ */
/* In an immersive session the headset owns the camera; the desktop
   OrbitControls must stand down so the two do not fight. */
export function XROrbitControls(props) {
  const session = useXR((xr) => xr.session);
  if (session != null) return null;   // headset owns the camera in VR
  return <OrbitControls {...props} />;
}
