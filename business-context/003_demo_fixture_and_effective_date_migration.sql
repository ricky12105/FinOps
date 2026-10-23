IF COL_LENGTH(N'dbo.DemoContextSource', N'TenantId') IS NULL
BEGIN
    ALTER TABLE dbo.DemoContextSource ADD TenantId uniqueidentifier NULL;
END;
GO

IF EXISTS (SELECT 1 FROM dbo.DemoContextSource WHERE TenantId IS NULL)
BEGIN
    UPDATE source
    SET TenantId = resource.TenantId
    FROM dbo.DemoContextSource AS source
    INNER JOIN dbo.BusinessContextResource AS resource
        ON resource.ResourceKey = source.ResourceKey;
END;
GO

IF EXISTS (SELECT 1 FROM dbo.DemoContextSource WHERE TenantId IS NULL)
    THROW 51000, 'Unable to resolve tenant for every demo context source row.', 1;
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.DemoContextSource') AND name = N'TenantId' AND is_nullable = 1)
BEGIN
    ALTER TABLE dbo.DemoContextSource ALTER COLUMN TenantId uniqueidentifier NOT NULL;
END;
GO

IF COL_LENGTH(N'dbo.BusinessContextOption', N'IsDemoFixture') IS NULL
BEGIN
    ALTER TABLE dbo.BusinessContextOption ADD IsDemoFixture bit NOT NULL
    CONSTRAINT DF_BusinessContextOption_IsDemoFixture DEFAULT (1) WITH VALUES;
END;
GO

IF COL_LENGTH(N'dbo.BusinessContextAssignment', N'IsDemoFixture') IS NULL
BEGIN
    ALTER TABLE dbo.BusinessContextAssignment ADD IsDemoFixture bit NOT NULL
        CONSTRAINT DF_BusinessContextAssignment_IsDemoFixture DEFAULT (0);
END;
GO

IF OBJECT_ID(N'dbo.BusinessContextConfiguration', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.BusinessContextConfiguration (
        TenantId uniqueidentifier NOT NULL PRIMARY KEY,
        DemoFixturesEnabled bit NOT NULL CONSTRAINT DF_BusinessContextConfiguration_DemoFixtures DEFAULT (0),
        UpdatedAtUtc datetime2(3) NOT NULL CONSTRAINT DF_BusinessContextConfiguration_UpdatedAt DEFAULT (SYSUTCDATETIME())
    );
END;
GO

INSERT dbo.BusinessContextConfiguration (TenantId, DemoFixturesEnabled)
SELECT DISTINCT resource.TenantId, 1
FROM dbo.BusinessContextResource AS resource
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.BusinessContextConfiguration AS config WHERE config.TenantId = resource.TenantId
);
GO