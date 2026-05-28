# Interface Contracts

## Auth Service → API Gateway

### JWT Validation Middleware

```typescript
// Contract: auth-service exposes this middleware signature
export interface AuthMiddleware {
  validateToken(token: string): Promise<TokenPayload>;
  requireRole(role: UserRole): RequestHandler;
}

export interface TokenPayload {
  userId: string;
  email: string;
  roles: UserRole[];
  exp: number;
  iat: number;
}

export type UserRole = "admin" | "customer" | "vendor";
```

### User Model (shared type)

```typescript
export interface User {
  id: string;
  email: string;
  name: string;
  roles: UserRole[];
  createdAt: Date;
  updatedAt: Date;
}
```

---

## Product Catalog → API Gateway

### Product API

```typescript
export interface ProductService {
  getProduct(id: string): Promise<Product | null>;
  searchProducts(query: SearchQuery): Promise<PaginatedResult<Product>>;
  createProduct(input: CreateProductInput): Promise<Product>;
  updateProduct(id: string, input: UpdateProductInput): Promise<Product>;
}

export interface Product {
  id: string;
  name: string;
  description: string;
  price: number;  // cents
  currency: string;  // ISO 4217
  category: string;
  inStock: boolean;
  createdAt: Date;
}

export interface SearchQuery {
  term?: string;
  category?: string;
  minPrice?: number;
  maxPrice?: number;
  page: number;
  pageSize: number;
}

export interface PaginatedResult<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasNext: boolean;
}
```

---

## Notification Service → API Gateway

### Event Publisher

```typescript
export interface NotificationService {
  sendEmail(params: EmailParams): Promise<void>;
  sendPush(params: PushParams): Promise<void>;
  publishEvent(event: DomainEvent): Promise<void>;
}

export interface EmailParams {
  to: string;
  subject: string;
  templateId: string;
  variables: Record<string, string>;
}

export interface PushParams {
  userId: string;
  title: string;
  body: string;
  data?: Record<string, string>;
}

export interface DomainEvent {
  type: string;
  payload: Record<string, unknown>;
  timestamp: Date;
  correlationId: string;
}
```

---

## Contract Freeze Notice

**Status**: FROZEN as of 2026-05-28

These contracts MUST NOT be modified by any parallel agent without halting all dependent units and obtaining orchestrator approval. Any change requires:

1. Notify orchestrator
2. Halt all agents consuming the modified contract
3. Update contract document
4. Resume agents with updated contract reference
