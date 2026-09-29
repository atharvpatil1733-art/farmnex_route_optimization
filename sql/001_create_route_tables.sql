-- FarmNex route optimization tables.
-- Only CREATE ... IF NOT EXISTS: this never drops, alters or touches existing FarmNex tables.
-- Run once in Supabase: Dashboard -> SQL Editor -> paste -> Run.
-- (The package can also create these itself on first request when ROUTES_AUTO_CREATE_TABLES=true.)

create table if not exists rt_vehicles (
    id              varchar(64) primary key,   -- same id as the vehicle in the main app
    driver_user_id  varchar(64),
    driver_name     varchar(120),
    driver_phone    varchar(20),
    owner_role      varchar(20)  not null default 'transporter',
    vehicle_number  varchar(20)  not null,
    vehicle_type    varchar(20)  not null default 'tempo',
    capacity_kg     double precision not null,
    refrigerated    boolean not null default false,
    rate_per_ton_km double precision,          -- current rate, sent by the main app
    base_lat        double precision not null,
    base_lng        double precision not null,
    base_label      varchar(200),
    status          varchar(20) not null default 'available',
    last_lat        double precision,
    last_lng        double precision,
    last_seen_at    timestamptz,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists ix_rt_vehicles_driver_user_id on rt_vehicles (driver_user_id);

create table if not exists rt_loads (
    id              varchar(36) primary key,
    order_id        varchar(64),
    farmer_id       varchar(64),
    farmer_name     varchar(120) not null,
    farmer_phone    varchar(20),
    buyer_id        varchar(64),
    buyer_name      varchar(120) not null,
    buyer_phone     varchar(20),
    crop            varchar(60)  not null,
    weight_kg       double precision not null,
    needs_cold      boolean not null default false,
    priority        integer not null default 0,
    pickup_lat      double precision not null,
    pickup_lng      double precision not null,
    pickup_address  varchar(255) not null,
    drop_lat        double precision not null,
    drop_lng        double precision not null,
    drop_address    varchar(255) not null,
    status          varchar(20) not null default 'pending',
    estimated_fare  double precision,
    trip_id         varchar(36),
    created_at      timestamptz not null default now(),
    delivered_at    timestamptz
);
create index if not exists ix_rt_loads_status on rt_loads (status);
create index if not exists ix_rt_loads_order_id on rt_loads (order_id);
-- One live delivery per main-app order, even if two requests race.
create unique index if not exists ux_rt_loads_active_order on rt_loads (order_id)
    where order_id is not null and status <> 'cancelled';

create table if not exists rt_trips (
    id                 varchar(36) primary key,
    vehicle_id         varchar(64) not null references rt_vehicles (id),
    status             varchar(20) not null default 'planned',
    is_backhaul        boolean not null default false,
    total_distance_km  double precision not null default 0,
    total_duration_min double precision not null default 0,
    estimated_cost     double precision not null default 0,
    routing_source     varchar(20) not null default 'estimate',
    start_lat          double precision not null,
    start_lng          double precision not null,
    geometry           json,
    created_at         timestamptz not null default now(),
    started_at         timestamptz,
    completed_at       timestamptz
);
create index if not exists ix_rt_trips_vehicle_status on rt_trips (vehicle_id, status);

create table if not exists rt_trip_stops (
    id                  varchar(36) primary key,
    trip_id             varchar(36) not null references rt_trips (id) on delete cascade,
    load_id             varchar(36) not null references rt_loads (id),
    seq                 integer not null,
    kind                varchar(10) not null,
    lat                 double precision not null,
    lng                 double precision not null,
    label               varchar(255) not null,
    leg_distance_km     double precision not null,
    leg_duration_min    double precision not null,
    planned_arrival_min double precision not null,
    status              varchar(10) not null default 'pending',
    done_at             timestamptz
);
create index if not exists ix_rt_trip_stops_trip on rt_trip_stops (trip_id, seq);

create table if not exists rt_locations (
    id           serial primary key,
    vehicle_id   varchar(64) not null references rt_vehicles (id),
    trip_id      varchar(36),
    lat          double precision not null,
    lng          double precision not null,
    speed_kmph   double precision,
    heading      double precision,
    recorded_at  timestamptz not null default now()
);
create index if not exists ix_rt_locations_vehicle_time on rt_locations (vehicle_id, recorded_at);

create table if not exists rt_notifications (
    id          varchar(36) primary key,
    vehicle_id  varchar(64) not null references rt_vehicles (id),
    kind        varchar(30) not null,
    load_id     varchar(36),
    title       varchar(200) not null,
    message     text not null,
    payload     json,
    is_read     boolean not null default false,
    created_at  timestamptz not null default now()
);
create index if not exists ix_rt_notifications_vehicle on rt_notifications (vehicle_id, is_read);

-- Keep these tables off Supabase's public REST API (anon / authenticated keys).
-- The backend connects as the table owner, which bypasses row level security, so it keeps working.
alter table rt_vehicles      enable row level security;
alter table rt_loads         enable row level security;
alter table rt_trips         enable row level security;
alter table rt_trip_stops    enable row level security;
alter table rt_locations     enable row level security;
alter table rt_notifications enable row level security;
