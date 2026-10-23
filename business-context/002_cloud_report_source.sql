IF OBJECT_ID(N'dbo.DemoBilling', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DemoBilling (
        RowId bigint NOT NULL PRIMARY KEY,
        ContextKey bigint NOT NULL,
        Date date NOT NULL,
        Period char(7) NOT NULL,
        Meter nvarchar(512) NOT NULL,
        ChargeCategory nvarchar(100) NOT NULL,
        ChargeClass nvarchar(100) NOT NULL,
        BilledCost decimal(19,4) NOT NULL,
        ExactBilledCost nvarchar(64) NOT NULL,
        SourceRunId uniqueidentifier NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.DemoContextSource', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DemoContextSource (
        TenantId uniqueidentifier NOT NULL,
        ContextKey bigint NOT NULL PRIMARY KEY,
        ResourceKey nvarchar(128) NOT NULL UNIQUE,
        Resource nvarchar(256) NOT NULL,
        ResourceId nvarchar(2048) NOT NULL,
        ResourceGroup nvarchar(256) NOT NULL,
        Service nvarchar(256) NOT NULL,
        Platform nvarchar(128) NOT NULL,
        BusinessUnit nvarchar(200) NOT NULL,
        Initiative nvarchar(300) NOT NULL,
        Project nvarchar(300) NOT NULL,
        Tool nvarchar(200) NOT NULL,
        Attribution nvarchar(100) NOT NULL,
        CostScope nvarchar(100) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.DemoCalendar', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DemoCalendar (
        Date date NOT NULL PRIMARY KEY,
        Month char(7) NOT NULL,
        MonthName nvarchar(20) NOT NULL
    );
END;
GO

IF OBJECT_ID(N'dbo.DemoSnapshot', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.DemoSnapshot (
        Period char(7) NOT NULL PRIMARY KEY,
        SourceRows bigint NOT NULL,
        SourceBilledCost decimal(19,4) NOT NULL,
        ModelBilledCost decimal(19,4) NOT NULL,
        RoundingDelta decimal(19,4) NOT NULL,
        Status nvarchar(100) NOT NULL,
        SubmittedUTC nvarchar(50) NOT NULL,
        RunId uniqueidentifier NOT NULL
    );
END;
GO

CREATE OR ALTER VIEW dbo.BusinessContextCandidates
AS
    SELECT r.TenantId, r.ResourceKey, r.ResourceId, r.DisplayName,
           r.IsAssignable, r.HasCompleteSourceTags, r.Version AS ResourceVersion,
           s.Platform, s.Service, s.ResourceGroup, s.Attribution AS SourceAttribution
    FROM dbo.BusinessContextResource AS r
    INNER JOIN dbo.DemoContextSource AS s ON s.ResourceKey = r.ResourceKey;
GO

CREATE OR ALTER VIEW dbo.Context
AS
    SELECT s.ContextKey, s.Resource, s.ResourceId, s.ResourceGroup, s.Service, s.Platform,
        CASE WHEN s.Attribution = N'Demo mapping' AND COALESCE(c.DemoFixturesEnabled, 0) = 0 THEN N'Unassigned' ELSE s.BusinessUnit END AS BusinessUnit,
        CASE WHEN s.Attribution = N'Demo mapping' AND COALESCE(c.DemoFixturesEnabled, 0) = 0 THEN N'Unassigned' ELSE s.Initiative END AS Initiative,
        CASE WHEN s.Attribution = N'Demo mapping' AND COALESCE(c.DemoFixturesEnabled, 0) = 0 THEN N'Unassigned' ELSE s.Project END AS Project,
        CASE WHEN s.Attribution = N'Demo mapping' AND COALESCE(c.DemoFixturesEnabled, 0) = 0 THEN N'Unassigned' ELSE s.Tool END AS Tool,
        CASE WHEN s.Attribution = N'Demo mapping' AND COALESCE(c.DemoFixturesEnabled, 0) = 0 THEN N'Unassigned' ELSE s.Attribution END AS Attribution,
        s.CostScope
    FROM dbo.DemoContextSource AS s
    LEFT JOIN dbo.BusinessContextConfiguration AS c ON c.TenantId = s.TenantId
    UNION ALL
    SELECT CONVERT(bigint, 1000000000) + a.AssignmentId AS ContextKey,
           s.Resource, s.ResourceId, s.ResourceGroup, s.Service, s.Platform,
           o.BusinessUnit, o.Initiative, o.Project, o.Tool,
           N'Manual assignment' AS Attribution, s.CostScope
    FROM dbo.BusinessContextAssignment AS a
    INNER JOIN dbo.BusinessContextOption AS o
        ON o.TenantId = a.TenantId AND o.ContextOptionId = a.ContextOptionId
    INNER JOIN dbo.DemoContextSource AS s ON s.ResourceKey = a.ResourceKey
    LEFT JOIN dbo.BusinessContextConfiguration AS c ON c.TenantId = a.TenantId
    WHERE a.IsDemoFixture = 0 OR COALESCE(c.DemoFixturesEnabled, 0) = 1;
GO

CREATE OR ALTER VIEW dbo.Billing
AS
    SELECT b.RowId,
           CASE WHEN a.AssignmentId IS NULL THEN b.ContextKey
                ELSE CONVERT(bigint, 1000000000) + a.AssignmentId END AS ContextKey,
           b.Date, b.Period, b.Meter, b.ChargeCategory, b.ChargeClass,
           b.BilledCost, b.ExactBilledCost, CONVERT(nvarchar(36), b.SourceRunId) AS SourceRunId
    FROM dbo.DemoBilling AS b
        INNER JOIN dbo.DemoContextSource AS s ON s.ContextKey = b.ContextKey
        INNER JOIN dbo.BusinessContextResource AS r ON r.TenantId = s.TenantId AND r.ResourceKey = s.ResourceKey
        LEFT JOIN dbo.BusinessContextConfiguration AS c ON c.TenantId = r.TenantId
    OUTER APPLY (
        SELECT TOP (1) assignment.AssignmentId
        FROM dbo.BusinessContextAssignment AS assignment
                WHERE assignment.TenantId = r.TenantId AND assignment.ResourceKey = s.ResourceKey
          AND assignment.EffectiveFrom <= b.Date
                    AND (assignment.IsDemoFixture = 0 OR COALESCE(c.DemoFixturesEnabled, 0) = 1)
        ORDER BY assignment.EffectiveFrom DESC
    ) AS a;
GO

CREATE OR ALTER VIEW dbo.BusinessContextCurrent
AS
    SELECT r.TenantId, r.ResourceKey, s.ResourceId, r.DisplayName, r.Version AS ResourceVersion,
           a.ContextOptionId, o.BusinessUnit, o.Initiative, o.Project, o.Tool, a.EffectiveFrom
    FROM dbo.BusinessContextResource AS r
    INNER JOIN dbo.DemoContextSource AS s ON s.ResourceKey = r.ResourceKey
    OUTER APPLY (
        SELECT TOP (1) assignment.ContextOptionId, assignment.EffectiveFrom
        FROM dbo.BusinessContextAssignment AS assignment
        WHERE assignment.TenantId = r.TenantId AND assignment.ResourceKey = r.ResourceKey
          AND assignment.EffectiveFrom <= CONVERT(date, SYSUTCDATETIME())
                    AND (assignment.IsDemoFixture = 0 OR EXISTS (
                            SELECT 1 FROM dbo.BusinessContextConfiguration AS config
                            WHERE config.TenantId = r.TenantId AND config.DemoFixturesEnabled = 1
                    ))
        ORDER BY assignment.EffectiveFrom DESC
    ) AS a
    LEFT JOIN dbo.BusinessContextOption AS o
        ON o.TenantId = r.TenantId AND o.ContextOptionId = a.ContextOptionId;
GO