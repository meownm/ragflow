CREATE TABLE employees (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_id BIGINT REFERENCES employees(id)
);
CREATE TABLE customers (
    id BIGINT PRIMARY KEY,
    region TEXT,
    account_manager_id BIGINT REFERENCES employees(id)
);
CREATE TABLE categories (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id BIGINT REFERENCES categories(id)
);
CREATE TABLE products (
    id BIGINT PRIMARY KEY,
    name TEXT NOT NULL,
    category_id BIGINT NOT NULL REFERENCES categories(id)
);
CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(id),
    payer_id BIGINT REFERENCES customers(id),
    created_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL
);
CREATE TABLE order_items (
    id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    product_id BIGINT NOT NULL REFERENCES products(id),
    amount NUMERIC(18,2) NOT NULL
);
CREATE TABLE payments (
    id BIGINT PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id),
    amount NUMERIC(18,2) NOT NULL
);
CREATE TABLE shipments (
    id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    shipped_at TIMESTAMPTZ NOT NULL
);
