# Software Requirements Specification (SRS)

## Project: Decentralized LAN Chat Application

---

## 1. Introduction

### 1.1 Purpose

The purpose of this document is to define the functional and non-functional requirements for a decentralized Local Area Network (LAN) chat application. This document serves as a guide for the development, testing, and evaluation of the project.

### 1.2 Scope

This project aims to build a fully decentralized, serverless chat application designed for use on a single local network. It allows users to discover peers automatically, exchange text and files securely, and share formatted code snippets without relying on an external internet connection or centralized server.

### 1.3 Definitions and Acronyms

- **LAN:** Local Area Network
- **P2P:** Peer-to-Peer
- **E2EE:** End-to-End Encryption
- **UDP:** User Datagram Protocol
- **TCP:** Transmission Control Protocol
- **AES:** Advanced Encryption Standard

---

## 2. Overall Description

### 2.1 Product Perspective

The application operates entirely within a local network environment. It abandons the traditional client-server architecture in favor of a peer-to-peer mesh network, making it highly resilient to individual node failures.

### 2.2 User Classes and Characteristics

- **Standard Users:** Individuals needing quick, reliable, and private communication across a shared network (e.g., office environments, university dorms).
- **Developers/Students:** Technical users who will specifically benefit from the integrated code snippet sharing and execution features.

### 2.3 Operating Environment

The backend networking daemon will be cross-platform (Windows, macOS, Linux). The frontend will run in modern web browsers or as a locally packaged desktop application (e.g., via Electron or Tauri).

---

## 3. System Features (Functional Requirements)

### 3.1 Zero-Configuration Peer Discovery

- **Description:** Users must automatically discover other active instances of the application on the same subnet.
- **Technical Requirement:** The system shall use UDP multicast/broadcast (e.g., mDNS) to announce presence and discover peers without manual IP configuration.

### 3.2 Decentralized P2P Messaging

- **Description:** Text messages must route directly between users.
- **Technical Requirement:** The system shall establish direct TCP sockets between peers for communication. If one node disconnects, the rest of the network mesh must remain unaffected.

### 3.3 End-to-End Encryption (E2EE)

- **Description:** All communications must be secure from local packet sniffing.
- **Technical Requirement:** The system shall implement a key exchange protocol (e.g., Diffie-Hellman) and encrypt all payloads using AES-256 before transmission.

### 3.4 Peer-to-Peer File Transfer

- **Description:** Users must be able to send files directly to one another.
- **Technical Requirement:** The system shall open dedicated, high-bandwidth TCP streams for file transfers to maximize local network speeds.

### 3.5 Real-Time Code Snippet Sharing

- **Description:** A specialized module for sending and viewing programming code.
- **Technical Requirement:** The system shall support sending text blocks with language metadata, rendering them on the receiving end with proper syntax highlighting and formatting.

### 3.6 Presence and Heartbeat Monitoring

- **Description:** The application must accurately reflect who is online, offline, or away.
- **Technical Requirement:** Nodes shall periodically exchange lightweight "heartbeat" signals. If a node fails to send a heartbeat within a specified timeout threshold, it shall be marked as offline.

### 3.7 Offline Message Queuing (Store-and-Forward)

- **Description:** Users can send messages to peers who recently disconnected.
- **Technical Requirement:** The system shall store undelivered messages locally and automatically attempt delivery when the target peer is rediscovered on the network.

### 3.8 Network Diagnostics Dashboard

- **Description:** An interface providing real-time data on network health.
- **Technical Requirement:** The application shall display metrics including ping (latency) between peers, active socket connections, and packet transfer rates.

---

## 4. Non-Functional Requirements

### 4.1 Performance Requirements

- The application UI must not freeze or block during network operations (requires multithreading or asynchronous I/O).
- File transfers must utilize the maximum available bandwidth of the local router/switch.

### 4.2 Security Requirements

- No plain-text data shall be transmitted over the network.
- Encryption keys must be generated per session and never stored permanently on the disk.

### 4.3 Reliability and Availability

- The application must handle abrupt network disconnections gracefully without crashing.
- The decentralized nature dictates that the network must have 100% uptime as long as at least two nodes are active.

### 4.4 Usability

- The application requires zero setup or configuration files from the user prior to launch.
- The interface must clearly distinguish between standard text messages, system alerts, and code snippets.

---

## 5. Proposed Technology Stack

- **Networking / Backend:** Python (asyncio, sockets)
- **Frontend / UI:** Next.js.
- **Frontend-Backend Bridge:** WebSockets (for local communication between the UI and the underlying networking daemon).
- **Security:** Standard cryptographic libraries (e.g., `cryptography` in Python).
