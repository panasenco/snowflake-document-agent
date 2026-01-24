-- Creation and access control of database, schema, warehouse, and role for the document agent.
use role <% ctx.env.admin_role %>;
create database if not exists <% ctx.env.database %>;
create schema if not exists <% ctx.env.database %>.<% ctx.env.schema %>;
create warehouse if not exists <% ctx.env.warehouse %> with
    warehouse_size = <% ctx.env.warehouse_size %>
    auto_suspend = <% ctx.env.warehouse_auto_suspend %>;
create role if not exists <% ctx.env.role %>;
grant usage, operate on warehouse <% ctx.env.warehouse %> to role <% ctx.env.role %>;
grant usage on database <% ctx.env.database %> to role <% ctx.env.role %>;
grant usage on schema <% ctx.env.database %>.<% ctx.env.schema %> to role <% ctx.env.role %>;
grant usage, operate on warehouse <% ctx.env.warehouse %> to role <% ctx.env.role %>;
grant create table on schema <% ctx.env.database %>.<% ctx.env.schema %> to role <% ctx.env.role %>;
grant create stage on schema <% ctx.env.database %>.<% ctx.env.schema %> to role <% ctx.env.role %>;
grant role <% ctx.env.role %> to role <% ctx.env.admin_role %>;
