import React, { useMemo, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Html, Grid } from "@react-three/drei";
import * as THREE from "three";

const WATER = "#1d7fd4";
const WATER_GLOW = "#3fa9f5";
const STEEL = "#5b6b7d";
const SHELL = "#2c3d50";
const PIPE_R = 0.055;
const GREEN = "#3fb950";
const RED = "#ff7b72";
const AMBER = "#e3b341";

/* ------------------------------------------------------------------ */
/* geometry helper: cylinder oriented between two 3D points            */
/* ------------------------------------------------------------------ */
function segment(a, b) {
  const av = new THREE.Vector3(...a);
  const bv = new THREE.Vector3(...b);
  const dir = bv.clone().sub(av);
  const length = dir.length();
  const mid = av.clone().add(bv).multiplyScalar(0.5);
  const quat = new THREE.Quaternion().setFromUnitVectors(
    new THREE.Vector3(0, 1, 0),
    dir.normalize()
  );
  return { mid, quat, length };
}

/* ------------------------------------------------------------------ */
/* pipe segment with flow particles                                    */
/* ------------------------------------------------------------------ */
const N_BUBBLES = 8;

function PipeSegment({ a, b, flow }) {
  const { mid, quat, length } = useMemo(() => segment(a, b), [a, b]);
  const bubblesRef = useRef();
  const active = flow > 0.0005;

  useFrame((state) => {
    if (!active || !bubblesRef.current) return;
    const speed = 0.25 + flow * 90; // faster & denser-feeling at higher flow
    for (const bubble of bubblesRef.current.children) {
      const phase = (bubble.userData.phase + state.clock.elapsedTime * speed) % 1;
      bubble.position.y = (phase - 0.5) * length;
    }
  });

  return (
    <group>
      <mesh position={mid} quaternion={quat}>
        <cylinderGeometry args={[PIPE_R, PIPE_R, length, 14]} />
        <meshStandardMaterial color={STEEL} roughness={0.55} metalness={0.45} />
      </mesh>
      {active && (
        <group ref={bubblesRef} position={mid} quaternion={quat}>
          {Array.from({ length: N_BUBBLES }).map((_, i) => (
            <mesh
              key={i}
              position={[0, (i / N_BUBBLES - 0.5) * length, 0]}
              userData={{ phase: i / N_BUBBLES }}
            >
              <sphereGeometry args={[PIPE_R * 0.55, 8, 8]} />
              <meshBasicMaterial color={WATER_GLOW} />
            </mesh>
          ))}
        </group>
      )}
    </group>
  );
}

/* ------------------------------------------------------------------ */
/* selection / alarm beacons                                           */
/* ------------------------------------------------------------------ */
function SelectionRing({ position, radius }) {
  return (
    <mesh position={position} rotation={[-Math.PI / 2, 0, 0]}>
      <torusGeometry args={[radius, 0.06, 10, 48]} />
      <meshBasicMaterial color="#58a6ff" />
    </mesh>
  );
}

function Beacon({ position, color, active }) {
  const ref = useRef();
  useFrame((state) => {
    if (!ref.current) return;
    const s = 1 + 0.35 * Math.sin(state.clock.elapsedTime * 5);
    ref.current.scale.setScalar(active ? s : 0.0001);
  });
  return (
    <mesh ref={ref} position={position}>
      <sphereGeometry args={[0.22, 16, 16]} />
      <meshBasicMaterial color={color} />
    </mesh>
  );
}

/* ------------------------------------------------------------------ */
/* equipment                                                           */
/* ------------------------------------------------------------------ */
function Reservoir({ res, selected, onSelect }) {
  const y = res.y || 0;
  const h = res.height || 2.5;
  const r = res.radius || 1.1;
  const fill = h * 0.8; // effectively-infinite supply: static visual level
  return (
    <group position={[res.x, y, 0]} onClick={(e) => { e.stopPropagation(); onSelect("reservoir"); }}>
      <mesh position={[0, h / 2, 0]}>
        <cylinderGeometry args={[r, r, h, 32, 1, true]} />
        <meshStandardMaterial color={SHELL} transparent opacity={0.55} roughness={0.4} metalness={0.3} side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[0, fill / 2, 0]}>
        <cylinderGeometry args={[r * 0.92, r * 0.92, fill, 32]} />
        <meshStandardMaterial color="#2a4f7a" roughness={0.3} />
      </mesh>
      <Html center position={[0, h + 0.35, 0]} distanceFactor={12} style={{ pointerEvents: "none" }}>
        <div className="hmi-tag">RESERVOIR</div>
      </Html>
      {selected && <SelectionRing position={[0, 0.02, 0]} radius={r + 0.3} />}
    </group>
  );
}

function Tank({ tank, state, selected, alarmColor, onSelect }) {
  const { tag, radius, height, z_base, x, z } = tank;
  const level = state ? state.levels[tag] || 0 : 0;
  const pct = state ? state.levels_pct[tag] || 0 : 0;
  const liquidH = Math.max(0, Math.min(height, level));
  const emit = selected ? "#123a5c" : "#000000";
  const shellColor = alarmColor ? alarmColor : selected ? "#3f6f9f" : SHELL;

  return (
    <group
      position={[x, z_base, z]}
      onClick={(e) => { e.stopPropagation(); onSelect(tag); }}
      onPointerOver={() => (document.body.style.cursor = "pointer")}
      onPointerOut={() => (document.body.style.cursor = "auto")}
    >
      {/* liquid */}
      {liquidH > 0.002 && (
        <mesh position={[0, liquidH / 2, 0]}>
          <cylinderGeometry args={[radius * 0.88, radius * 0.88, liquidH, 32]} />
          <meshStandardMaterial color={WATER} roughness={0.2} emissive={WATER} emissiveIntensity={0.25} />
        </mesh>
      )}
      {/* shell (open-ended) */}
      <mesh position={[0, height / 2, 0]}>
        <cylinderGeometry args={[radius, radius, height, 32, 1, true]} />
        <meshStandardMaterial
          color={shellColor} emissive={emit} emissiveIntensity={selected ? 0.6 : 0}
          transparent opacity={0.6} roughness={0.35} metalness={0.3} side={THREE.DoubleSide}
        />
      </mesh>
      {/* top rim */}
      <mesh position={[0, height, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[radius, 0.035, 8, 40]} />
        <meshStandardMaterial color={STEEL} roughness={0.5} metalness={0.5} />
      </mesh>
      <Html center position={[0, height + 0.4, 0]} distanceFactor={12} style={{ pointerEvents: "none" }}>
        <div className="hmi-tag">
          <b>{tag}</b>
          <span className="hmi-val">{level.toFixed(2)} m</span>
        </div>
      </Html>
      {selected && <SelectionRing position={[0, 0.02, 0]} radius={radius + 0.28} />}
      {alarmColor && <Beacon position={[0, height + 1.0, 0]} color={alarmColor} active />}
    </group>
  );
}

function Valve({ valve, state, selected, onSelect }) {
  const { tag, x, y, z } = valve;
  const v = state && state.valves[tag] ? state.valves[tag] : { eff: 0, blocked: false };
  const open = v.eff / 100;
  const fault = state && state.faults && state.faults.valve_stuck[tag];
  let color = open > 0.05 ? GREEN : "#7d8b99";
  if (v.blocked) color = AMBER;
  if (fault) color = RED;
  const discRot = (1 - open) * (Math.PI / 2);

  return (
    <group
      position={[x, y, z]}
      onClick={(e) => { e.stopPropagation(); onSelect(tag); }}
      onPointerOver={() => (document.body.style.cursor = "pointer")}
      onPointerOut={() => (document.body.style.cursor = "auto")}
    >
      {/* housing along X */}
      <mesh rotation={[0, 0, Math.PI / 2]}>
        <cylinderGeometry args={[0.15, 0.15, 0.34, 16]} />
        <meshStandardMaterial color={STEEL} roughness={0.5} metalness={0.5} />
      </mesh>
      {/* actuator stem */}
      <mesh position={[0, 0.22, 0]}>
        <cylinderGeometry args={[0.045, 0.045, 0.34, 10]} />
        <meshStandardMaterial color={STEEL} roughness={0.5} />
      </mesh>
      {/* rotating disc (butterfly) */}
      <group rotation={[0, discRot, 0]}>
        <mesh>
          <cylinderGeometry args={[0.16, 0.16, 0.03, 24]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={fault || v.blocked ? 0.7 : 0.25} roughness={0.4} />
        </mesh>
      </group>
      <Html center position={[0, -0.45, 0]} distanceFactor={12} style={{ pointerEvents: "none" }}>
        <div className="hmi-tag">
          <b>{tag}</b>
          <span className="hmi-val">{Math.round(v.eff)} %</span>
        </div>
      </Html>
      {selected && <SelectionRing position={[0, 0.02, 0]} radius={0.4} />}
    </group>
  );
}

function Pump({ pump, state, selected, onSelect }) {
  const { tag, x, y, z } = pump;
  const eff = state && state.pump ? state.pump.eff || 0 : 0;
  const running = eff > 0.5;
  const tripped = state && state.faults && state.faults.pump_tripped;
  const impeller = useRef();

  useFrame((_, delta) => {
    if (impeller.current && running) {
      impeller.current.rotation.y += delta * (6 + (eff / 100) * 30);
    }
  });

  let color = running ? GREEN : "#7d8b99";
  if (tripped) color = RED;

  return (
    <group
      position={[x, y, z]}
      onClick={(e) => { e.stopPropagation(); onSelect(tag); }}
      onPointerOver={() => (document.body.style.cursor = "pointer")}
      onPointerOut={() => (document.body.style.cursor = "auto")}
    >
      {/* motor body */}
      <mesh>
        <cylinderGeometry args={[0.34, 0.34, 0.55, 20]} />
        <meshStandardMaterial color={STEEL} roughness={0.45} metalness={0.5} />
      </mesh>
      {/* status ring */}
      <mesh position={[0, 0.32, 0]} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.3, 0.05, 8, 24]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.8} />
      </mesh>
      {/* impeller */}
      <group ref={impeller}>
        <mesh position={[0, 0, 0]}>
          <cylinderGeometry args={[0.26, 0.26, 0.07, 6]} />
          <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.4} metalness={0.6} />
        </mesh>
      </group>
      <Html center position={[0, 0.75, 0]} distanceFactor={12} style={{ pointerEvents: "none" }}>
        <div className="hmi-tag">
          <b>{tag}</b>
          <span className="hmi-val">{Math.round(eff)} %</span>
        </div>
      </Html>
      {selected && <SelectionRing position={[0, 0.02, 0]} radius={0.55} />}
    </group>
  );
}

/* ------------------------------------------------------------------ */
/* alarm -> equipment mapping (presentation only, no physics)          */
/* ------------------------------------------------------------------ */
function alarmEquipment(alarm) {
  const t = alarm.tag;
  if (t === "ESTOP") return null;
  if (/^P-|TRIP/.test(t)) return "P-101";
  if (/VALVE/.test(t)) return "XV-101"; // travel fault highlights the first valve
  if (/^LT-/.test(t)) return "LT-" + t.slice(-3);
  const m = t.match(/(\d{3})$/);
  if (m && /^L[ST]/.test(t)) return "TK-" + m[1];
  return null;
}

function alarmColorFor(priority) {
  if (priority === "CRITICAL") return RED;
  if (priority === "HIGH") return AMBER;
  return AMBER;
}

/* ------------------------------------------------------------------ */
/* scene root                                                          */
/* ------------------------------------------------------------------ */
export default function PlantScene({ config, state, selected, onSelect }) {
  const pipes = useMemo(() => (config ? buildPipes(config) : []), [config]);
  const tanksById = useMemo(
    () => (config ? Object.fromEntries(config.tanks.map((t) => [t.tag, t])) : {}),
    [config]
  );

  if (!config) {
    return <div className="vp-hint">Loading plant configuration…</div>;
  }

  // per-equipment alarm colour (highest priority wins)
  const equipAlarm = {};
  for (const a of state?.alarms || []) {
    const eq = alarmEquipment(a);
    if (eq) equipAlarm[eq] = alarmColorFor(a.priority);
  }

  const beaconPositions = [];
  const seen = new Set();
  for (const a of state?.alarms || []) {
    const eq = alarmEquipment(a);
    if (!eq || seen.has(eq)) continue;
    seen.add(eq);
    const pos = equipmentTop(eq, config, tanksById);
    if (pos) beaconPositions.push({ eq, pos, color: alarmColorFor(a.priority) });
  }

  return (
    <Canvas shadows camera={{ position: [4, 8, 20], fov: 45 }} gl={{ antialias: true }}>
      <color attach="background" args={["#07090d"]} />
      <ambientLight intensity={0.5} />
      <hemisphereLight args={["#8fb0d8", "#0a0e14", 0.5]} />
      <directionalLight
        position={[12, 20, 10]}
        intensity={1.5}
        castShadow
        shadow-mapSize-width={2048}
        shadow-mapSize-height={2048}
        shadow-camera-left={-30}
        shadow-camera-right={30}
        shadow-camera-top={30}
        shadow-camera-bottom={-10}
      />
      <OrbitControls
        target={[3.5, 1.6, 0]}
        maxPolarAngle={Math.PI * 0.49}
        minDistance={4}
        maxDistance={50}
        enableDamping
      />

      <Grid
        position={[3, -0.03, 0]}
        args={[40, 40]}
        cellSize={0.5}
        cellColor="#16202b"
        sectionSize={2.5}
        sectionColor="#263344"
        fadeDistance={45}
        infiniteGrid
      />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[3, -0.04, 0]} receiveShadow>
        <planeGeometry args={[80, 80]} />
        <meshStandardMaterial color="#0a0e14" roughness={0.9} />
      </mesh>

      {/* pipes */}
      {pipes.map((p) => (
        <PipeSegment key={p.key} a={p.a} b={p.b} flow={state ? state.flows[p.flowKey] || 0 : 0} />
      ))}

      {/* equipment */}
      <Reservoir res={config.reservoir} selected={selected === "reservoir"} onSelect={onSelect} />
      <Pump pump={config.pump} state={state} selected={selected === "P-101"} onSelect={onSelect} />
      {config.tanks.map((t) => (
        <Tank
          key={t.tag}
          tank={t}
          state={state}
          selected={selected === t.tag}
          alarmColor={equipAlarm[t.tag]}
          onSelect={onSelect}
        />
      ))}
      {config.valves.map((v) => (
        <Valve key={v.tag} valve={v} state={state} selected={selected === v.tag} onSelect={onSelect} />
      ))}

      {/* drain riser */}
      <mesh position={[config.drain.x, 0.05, 0]}>
        <cylinderGeometry args={[PIPE_R, PIPE_R, 0.6, 12]} />
        <meshStandardMaterial color={STEEL} roughness={0.5} />
      </mesh>
      <Html center position={[config.drain.x, -0.6, 0]} distanceFactor={12} style={{ pointerEvents: "none" }}>
        <div className="hmi-tag">DRAIN</div>
      </Html>

      {/* alarm beacons */}
      {beaconPositions.map((b) => (
        <Beacon key={b.eq} position={b.pos} color={b.color} active />
      ))}
    </Canvas>
  );
}

/* ------------------------------------------------------------------ */
/* pipe topology from config                                           */
/* ------------------------------------------------------------------ */
function buildPipes(config) {
  const tanks = Object.fromEntries(config.tanks.map((t) => [t.tag, t]));
  const res = config.reservoir;
  const drain = config.drain;
  const pump = config.pump;
  const pipes = [];

  const tk1 = tanks[pump.downstream] || tanks["TK-101"];
  pipes.push({
    key: "P-101-suction",
    a: [res.x + res.radius, 0.35, 0],
    b: [pump.x - 0.5, pump.y, 0],
    flowKey: "P-101",
  });
  pipes.push({
    key: "P-101-discharge",
    a: [pump.x + 0.5, pump.y, 0],
    b: [tk1.x - tk1.radius, tk1.z_base + tk1.height - 0.3, 0],
    flowKey: "P-101",
  });

  for (const v of config.valves) {
    const up = tanks[v.upstream];
    if (!up) continue;
    const upOut = [up.x + up.radius, up.z_base + 0.2, 0];
    const vp = [v.x, v.y, 0];
    let downIn;
    if (v.downstream === "drain") {
      downIn = [drain.x - 0.6, 0.15, 0];
    } else {
      const down = tanks[v.downstream];
      if (!down) continue;
      downIn = [down.x - down.radius, down.z_base + 0.2, 0];
    }
    pipes.push({ key: v.tag + "-in", a: upOut, b: vp, flowKey: v.tag });
    pipes.push({ key: v.tag + "-out", a: vp, b: downIn, flowKey: v.tag });
  }
  return pipes;
}

function equipmentTop(eq, config, tanksById) {
  if (eq === "P-101") return [config.pump.x, config.pump.y + 1.0, 0];
  if (tanksById[eq]) return [tanksById[eq].x, tanksById[eq].z_base + tanksById[eq].height + 0.9, 0];
  const v = config.valves.find((x) => x.tag === eq);
  if (v) return [v.x, v.y + 0.7, 0];
  if (eq === "reservoir") return [config.reservoir.x, (config.reservoir.height || 2.5) + 1.0, 0];
  // LT-xxx sensor -> its tank
  const tk = tanksById[eq.replace("LT-", "TK-")];
  if (tk) return [tk.x, tk.z_base + tk.height + 0.9, 0];
  return null;
}
