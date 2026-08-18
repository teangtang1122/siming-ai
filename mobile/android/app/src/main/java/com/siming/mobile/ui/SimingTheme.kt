package com.siming.mobile.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

val SimingCinnabar = Color(0xFF8F3833)
val SimingInk = Color(0xFF252421)
val SimingPaper = Color(0xFFF7F6F2)
val SimingPaperWarm = Color(0xFFFFFCF6)
val SimingBlue = Color(0xFF315F75)
val SimingGreen = Color(0xFF39735D)
val SimingSurfaceRaised = Color(0xFFFFFEFB)
val SimingSurfaceMuted = Color(0xFFF0EEE8)
val SimingInkMuted = Color(0xFF706D66)

private val SimingLightColors = lightColorScheme(
    primary = SimingCinnabar,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFF5E6E2),
    onPrimaryContainer = Color(0xFF56201E),
    secondary = SimingBlue,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE9F2F6),
    onSecondaryContainer = Color(0xFF234B5D),
    tertiary = SimingGreen,
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFE8F2ED),
    onTertiaryContainer = Color(0xFF2B5947),
    background = SimingPaper,
    onBackground = SimingInk,
    surface = SimingSurfaceRaised,
    onSurface = SimingInk,
    surfaceVariant = SimingSurfaceMuted,
    onSurfaceVariant = SimingInkMuted,
    outline = Color(0xFFD4D1C9),
    outlineVariant = Color(0xFFE8E5DE),
    error = Color(0xFFB13B36),
)

private val SimingTypography = Typography(
    displaySmall = TextStyle(fontSize = 30.sp, lineHeight = 38.sp, fontWeight = FontWeight.SemiBold),
    headlineSmall = TextStyle(fontSize = 24.sp, lineHeight = 31.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 27.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 17.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 15.sp, lineHeight = 21.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 25.sp, fontWeight = FontWeight.Normal),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 22.sp, fontWeight = FontWeight.Normal),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.Normal),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 17.sp, fontWeight = FontWeight.Medium),
    labelSmall = TextStyle(fontSize = 10.sp, lineHeight = 14.sp, fontWeight = FontWeight.Medium),
)

private val SimingShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(18.dp),
    large = RoundedCornerShape(24.dp),
    extraLarge = RoundedCornerShape(30.dp),
)

@Composable
fun SimingTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = SimingLightColors,
        typography = SimingTypography,
        shapes = SimingShapes,
        content = content,
    )
}
