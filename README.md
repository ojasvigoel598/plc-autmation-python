# 🏭 Open-Source Python PLC Engine & Industrial Water Plant Simulator

## Overview

An open-source industrial automation framework written in Python that replicates the internal behavior of a Programmable Logic Controller (PLC). It simulates scan-cycle execution, discrete I/O mapping, and closed-loop control applied to a dynamic water tank system.

The goal is to demonstrate how real industrial automation systems (used in plants, refineries, and infrastructure systems) can be modeled/verified without proprietary PLC hardware.

Unlike rigid open-source soft-PLCs like openPLC that are confined strictly to logical execution, this Python-based environment allows engineers to embed continuous physics equations directly alongside control logic to evaluate immediate physical system impacts adn take in account of hydraulics . For example, a standard open-source PLC cannot natively solve the ordinary differential equations governing fluid dynamics or automatically generate real-time engineering graphs of a pipeline blowout caused by water hammer.

---

## 💡 Problem Statement

Industrial systems such as water treatment plants and chemical processing units require:

- Real-time control of fluid levels  
- High reliability and safety interlocks  
- Deterministic execution cycles  

Traditionally, this requires expensive proprietary PLC systems (Siemens, Allen-Bradley), making experimentation and learning difficult.

---

## ✅ Solution

This project implements a **software-based PLC engine** that simulates:

- IEC-style scan cycle execution  
- Digital input/output mapping (`I0.x`, `Q0.x`, `M0.x`)  
- SR flip-flops and timer blocks (TON/TOF)  
- Closed-loop feedback control  
- Fault injection and safety shutdown logic  

It applies these concepts to a real-time **water tank automation system**.

---

## ⚙️ Why Python?

| Reason | Benefit |
|--------|---------|
| Rapid prototyping | Test PLC logic instantly |
| Visualization | Real-time system graphs |
| Debugging | Easy inspection of logic states |
| Zero cost | No PLC hardware required |
| Portability | Runs on any system with Python |

---

## 🏗️ System Architecture

A real PLC operates in a deterministic scan cycle. This project replicates that behavior:

### 1. Input Scan
- Reads analog tank level
- Converts to digital sensors (`I0.0`, `I0.2`, etc.)

### 2. Logic Execution
- Runs ladder logic simulation
- Uses SR flip-flops and timers

### 3. Output Update
- Updates actuator signals (`Q0.0` pump)

### 4. Physical Plant Simulation
- Applies fluid dynamics (tank fill/drain)
## project structure
│
├── main.py
├── simulation.py
├── plc_logic.py
├── controllers.py
├── config.py
├── plc_advanced_module.py
│
├── results (graph)
## Key Features
IEC 61131-3 style PLC simulation
Closed-loop feedback control system
Industrial sensor abstraction layer
Fault injection and safety logic
SCADA-style visualization outputs
## Future Improvements
Real-time SCADA dashboard (live UI)
Multi-tank system simulation
PID controller integration
Web-based control panel
## License

Open-source project (MIT License recommended)


---

## 🚀 Getting Started

### Prerequisites

```bash
pip install numpy matplotlib

git clone https://github.com/YOUR_USERNAME/plc-water-tank-automation.git
cd plc-water-tank-automation
python main.py
